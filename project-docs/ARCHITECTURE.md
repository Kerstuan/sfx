# Архитектура
- Модель (`sfx/model.py`): Project → Composition → Layer → Property(keyframes, expr). Времена — кадры; ключи относительно layer.start.
  Свойства эффектов `fx/<fid>/<param>`, масок `mask/<id>/<k>`, фигур `shape/<sid>/<k>`, операторов `shape/<sid>/op_<oid>_<param>`.
- Editor (`editor.py`): состояние, выделение, undo (JSON-снимки), clipboard, кэш кадров, пререндер, прокси, сигналы Qt.
- Рендер (`render.py`): render_frame(comp, frame, pool, scale) на QPainter + numpy-эффекты (`effects.py`, FILTERS dict).
- Превью: Viewer → Editor.preview_image → RAM-кэш (`cache.py`) / черновик / фоновые процессы (`render_service.py` ↔ `render_worker.py`, pipe-протокол).
- Медиа: `media.py` (PyAV, MediaPool, прокси через `proxy.py`, NVENC all-intra). Звук: `audio.py` (ffmpeg-декод, mix, стрим-плеер, beats()).
- Фигуры: `shapes.py` (пути, отрисовка, группы) + `shape_ops.py` (Trim/Repeater/Offset/Pucker/Round/ZigZag/Twist/Wiggle/Merge).
- GPU: `gpu.py` (OpenGL blur в воркерах). Экспорт: `export.py` (ExportThread → ffmpeg, NVENC).
- UI: `ui/main_window.py` (меню, хоткеи, LayoutShortcuts для не-латинских раскладок, рабочие среды), `ui/timeline.py`, `ui/viewer.py`, `ui/effect_controls.py`, `ui/flow.py`.
- Плагины (`plugins.py`, API v1): ~/.config/sfx/plugins/*.py с PLUGIN={id,name,version,api} и register(api); api.add_effect → EFFECTS+FILTERS (тип «id/имя»), api.add_shape_op → shape_ops.OPS+CUSTOM. Ошибки ловит _safe (кадр не падает). Загрузка: main.py и render_worker.py. Вкл/выкл: QSettings plugins_disabled.
