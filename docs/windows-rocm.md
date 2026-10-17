# Windows, RX 6600 и portable v0.39.0

На этом компьютере проверены Windows 11 IoT Enterprise LTSC, Radeon RX 6600
(`gfx1032`, 8 ГБ VRAM), 32 ГБ RAM и драйвер AMD 32.0.21045.11001.
Сопоставление RX 6600 → gfx1032 опубликовано в
[TheRock RELEASES](https://github.com/ROCm/TheRock/blob/main/RELEASES.md),
а состояние Windows-сборок — в
[SUPPORTED_GPUS](https://github.com/ROCm/TheRock/blob/main/SUPPORTED_GPUS.md).

1. Из [релиза ComfyUI v0.39.0](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.39.0)
   скачан `ComfyUI_windows_portable_amd.7z` и распакован в эту папку проекта.
   SHA256 архива: `19be1e3c8690c4f9eeefb0f2ee8f835a98e2fbf33cd9b80d0ca309f4c86d37c1`.
2. В embedded Python 3.12.10 установлены согласованные nightly-пакеты AMD
   от 2026-10-13. PyTorch и torchvision используют extra `device-gfx1032`,
   который устанавливает пакеты именно для этой архитектуры.
3. Применена совместимость ComfyUI с gfx1032: существующий путь распаковки
   слоя вместо native INT8 GEMM, который здесь выдаёт ошибку hipBLASLt.
4. Сервер запускается через `scripts/Start-ComfyUI.ps1` или автоматически
   из `Generate-3D.ps1`; адрес — `http://127.0.0.1:8188`.

Установленные версии: `torch 2.13.0+rocm10.2.0a20261013`,
`torchvision 0.28.0+rocm10.2.0a20261013`,
`torchaudio 2.11.0.3+rocm10.2.0a20261013`.
Пакеты `rocm-sdk-core`, `rocm-sdk-libraries`, `rocm-sdk-device-gfx1032`,
`amd-torch-device-gfx1032` и `amd-torchvision-device-gfx1032` имеют ту же дату.
Источник — [официальный nightly-индекс AMD](https://nightly.repo.amd.com/rocm/whl-next/).

Повторная установка зафиксированных пакетов:

```powershell
cd C:\AI\Dart3s
.\scripts\Install-ROCm.ps1
```

Скрипт использует локальные wheels из `downloads/amd-wheels`, выполняет GPU-probe
и применяет `configure_rocm_compat.py`. Перед переустановкой остановите сервер;
после неё запустите его снова. Скрипт совместимости сохраняет исходный файл
и отказывается менять неизвестную версию функции.

CLI 1.22.0 установлен отдельно в `.venv-cli` согласно
[Comfy CLI getting started](https://docs.comfy.org/comfy-cli/getting-started).
Skills `comfy`, `comfy-debug`, `comfy-relay` установлены через
`comfy skill install --scope project --target agents-md` в `AGENTS.md`.

Проверки оборудования и реального quantized слоя сохранены в
`logs/gpu-probe.json` и `logs/quantized-linear-probe.log`.
На RX 6600 ComfyUI использует FP32 для Trellis2/Pixal3D; высокое разрешение
может давать длительные этапы генерации.
