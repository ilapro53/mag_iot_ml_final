# Запуск визуализатора фермы (4 живых графика) через uv.
# Зависимости (paho-mqtt, matplotlib) uv подтянет сам из заголовка PEP 723 в visualize.py —
# отдельное окружение создавать не нужно.
#
# Живое окно:   .\viz\run.ps1
# Сохранить PNG: .\viz\run.ps1 --save viz\out\graphs.png --duration 60
# Другой порт:   .\viz\run.ps1 --port 1883
uv run --script "$PSScriptRoot\visualize.py" @args
