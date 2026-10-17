# Параметры пайплайна

Исходник — официальный `3d_pixal3d_trellis2_image_to_model`; неизменённая копия хранится в `downloads/template.original.json`.
Сценарий `scripts/build_workflow.py` применяет к нему рецепт `workflows/image-to-3d.recipe.json` через `comfy workflow apply`.
Полученный `workflows/image-to-3d.json` можно открыть на холсте ComfyUI. Копия `Dart3s Image to 3D.json` сохранена в каталоге пользовательских workflow установленного ComfyUI.

В панели **Favorited Inputs** доступны:

| Параметр | Значение по умолчанию | Назначение |
|---|---:|---|
| Polygon count | 10000 | Целевое число треугольников после Decimate Mesh |
| Structure seed | 56 | Seed генерации начальной структуры |
| Shared geometry seed | 42 | Один источник для обоих геометрических KSampler |
| Texture generation seed | 43 | Seed генерации текстур |
| Remesh resolution | 256 | Разрешение сетки Remesh |
| Model | false | `false` — Pixal3D, `true` — Trellis.2, как в официальном шаблоне |
| Texture size | 256 | Квадратные текстуры PBR: базовый цвет, metallic, roughness, normal и AO; то же значение получает UV-развёртка |
| Source image | viking_wolf_rune_axe.png | Исходное изображение |

Связи seed и размера текстур сохранены в самом графе: изменение одного избранного значения обновляет все соответствующие потребители.
На пути генерации узлы Preview Image, Mask Preview и Get Mesh Info заменены прямыми связями от их производителей к потребителям.
Узлы предпросмотра и прочие ветки, которые не нужны для конечного Save 3D Advanced, выключены (`mode = 2`).
Сохранение GLB остаётся единственным активным выходным узлом.
По просьбе пользователя разрешение Trellis2 Upsample Stage снижено с 1536 до 1024. Это разрешение скрытого представления; Remesh по умолчанию остаётся 256.

Для подготовки другого графа:

```powershell
.\.venv-cli\Scripts\python.exe .\scripts\build_workflow.py --image input.png --model pixal3d --polygons 10000 --remesh-resolution 256 --texture-size 256 --structure-seed 56 --shape-seed 42 --texture-seed 43
```

Требуется запущенный сервер ComfyUI. Для офлайн-подготовки можно передать `--object-info logs/object_info.json`, полученный от этой установки ComfyUI.
Это только подготовка графа; генерацию запускает программа через Comfy CLI.
Рядом с готовым графом сохраняются `.apply.json` с результатом применения операций и `.parameters.json` с параметрами, избранными входами и проверками связей.

Формат избранных входов проверен по [официальному frontend v1.49.6](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.49.6/src/stores/workspace/favoritedWidgetsStore.ts):
`extra.favoritedWidgets.favorites` содержит пары `nodeLocatorId` и `widgetName`.
Для корневых узлов `nodeLocatorId` — строковое представление ID узла.
