"""Bounded CPU image heuristics, not a 3D quality or runtime predictor."""
from pathlib import Path
from statistics import median

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage as ndi
from .risk_version import RISK_VERSION
from .risk_history import matching_runs

VERSION = RISK_VERSION


def analyze_image(path, *, history_index=None, parameters=None):
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert('RGBA')
        original_size = list(image.size)
        image.thumbnail((256, 256), Image.Resampling.LANCZOS)
    pixels = np.asarray(image)
    rgb = pixels[..., :3].astype(np.float32)
    alpha = pixels[..., 3]
    transparent = float(np.mean(alpha < 250)) > .01
    uncertain = False
    if transparent:
        mask = alpha > 64
        method = 'alpha'
    else:
        # Only remove colour-connected background from the image border.
        # This never modifies the generation input or invokes a neural mask model.
        size = max(1, min(alpha.shape) // 16)
        corners = np.concatenate([rgb[:size, :size].reshape(-1, 3), rgb[:size, -size:].reshape(-1, 3),
                                  rgb[-size:, :size].reshape(-1, 3), rgb[-size:, -size:].reshape(-1, 3)])
        background = np.median(corners, axis=0)
        near = np.linalg.norm(rgb - background, axis=2) < 40
        seeds = np.zeros_like(near)
        seeds[0, :] = near[0, :]
        seeds[-1, :] = near[-1, :]
        seeds[:, 0] = near[:, 0]
        seeds[:, -1] = near[:, -1]
        mask = ~ndi.binary_propagation(seeds, mask=near)
        uncertain = float(np.median(np.linalg.norm(corners - background, axis=1))) > 25 or np.mean(mask) > .97
        method = 'border_colour'
    y, x = np.nonzero(mask)
    if len(x) < 16:
        return dict(version=VERSION, level='unknown', reasons=['Не удалось надёжно выделить предмет.'],
                    original_size=original_size, mask_method=method, metrics={})
    coords = np.column_stack((x, y)).astype(float)
    eigen = np.linalg.eigvalsh(np.cov(coords, rowvar=False))
    elongation = float(np.sqrt(max(eigen[-1], 1) / max(eigen[0], 1)))
    labels, _ = ndi.label(mask)
    components = int(np.sum(np.bincount(labels.ravel())[1:] >= 16))
    holes_mask = ndi.binary_fill_holes(mask) & ~mask
    holes_labels, _ = ndi.label(holes_mask)
    holes = int(np.sum(np.bincount(holes_labels.ravel())[1:] >= 9))
    interior = ndi.binary_erosion(mask, iterations=1)
    # Alpha pixels are composited only for this analysis, never saved over source.
    rgb = rgb * (alpha[..., None] / 255) + 255 * (1 - alpha[..., None] / 255)
    gray = rgb @ np.array([.299, .587, .114])
    gradient = np.hypot(ndi.sobel(gray, 0), ndi.sobel(gray, 1))
    detail = float(np.mean(gradient[interior] > 100)) if interior.any() else 0
    distances = ndi.distance_transform_edt(mask)[mask]
    thin = float(np.mean(distances <= 2))
    span = max(int(x.max()-x.min()+1), int(y.max()-y.min()+1))
    mass = float(distances.sum() / max(span ** 3, 1))
    reasons, score = [], 0
    if uncertain:
        reasons.append('Фон неоднородный или предмет сливается с фоном; оценка силуэта ненадёжна.')
    if components >= 4:
        score += 2
        reasons.append(f'Несколько раздельных областей ({components}): возможны мелкие отдельные детали или остатки фона.')
    if holes:
        score += 2 if holes >= 3 else 1
        reasons.append(f'Отверстия в силуэте ({holes}): риск потери просветов и соединения деталей.')
    if thin > .25 and elongation < 3:
        score += 2
        reasons.append('Заметная доля тонких участков и узких соединений.')
    if mass > .018 and elongation < 1.8:
        score += 2
        reasons.append('Крупные заполненные участки широкого силуэта: возможен большой объём вычисляемой структуры, даже при простом контуре.')
    if detail > .22 and elongation < 1.6:
        score += 2
        reasons.append('Много мелких контрастных деталей на широком силуэте; возможна лишняя геометрия.')
    elif detail > .35:
        score += 1
        reasons.append('Очень плотный рисунок деталей; часть линий может превратиться в геометрию.')
    if mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any():
        score += 1
        reasons.append('Предмет касается края картинки: проверьте, не обрезан ли он.')
    narrow = elongation >= 1.8 and mass < .012 and components <= 2 and holes <= 1
    level = 'unknown' if uncertain else 'high' if score >= 4 else 'medium' if score >= 2 else 'low' if narrow else 'unknown'
    if level == 'unknown':
        reasons.append('Недостаточно данных для низкого риска. Простой рисунок не означает лёгкую 3D-генерацию.')
    elif not reasons:
        reasons.append('Узкий вытянутый силуэт без обнаруженных сложных соединений; это предварительный признак, не гарантия.')
    result = dict(version=VERSION, level=level, score=score, reasons=reasons, original_size=original_size,
                mask_method=method, metrics=dict(components=components, holes=holes,
                elongation=round(elongation, 3), thin_fraction=round(thin, 3), detail_fraction=round(detail, 3),
                filled_mass_proxy=round(mass, 5)), basis='image_features')
    runs = matching_runs(path, history_index, parameters)[:5]
    if runs:
        durations = sorted(row['seconds'] for row in runs)
        slowest = durations[-1]
        typical = median(durations)
        result.update(level='high' if typical >= 300 else 'medium' if typical >= 180 else 'low',
                      basis='measured_history', history=dict(runs=len(runs), min_seconds=durations[0],
                      max_seconds=slowest, median_seconds=typical, metadata_paths=[row['metadata_path'] for row in runs]))
        result['reasons'].insert(0, f'История этой картинки с теми же настройками: полных запусков — {len(runs)} (до 5 последних). '
            f'Медианное время {round(typical)//60} мин {round(typical)%60:02d} с; диапазон '
            f'{round(durations[0])//60} мин {round(durations[0])%60:02d} с — {round(slowest)//60} мин {round(slowest)%60:02d} с серверного расчёта. '
            'Новый запуск может отличаться; это не оценка художественного качества.')
    return result
