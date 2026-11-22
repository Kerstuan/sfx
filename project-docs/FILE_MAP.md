# Карта файлов (sfx/)
| Система | Файл |
|---|---|
| Модель, свойства, подписи | model.py |
| Действия редактора, undo, clipboard, кэш, прекомп | editor.py |
| Рендер кадра | render.py |
| Эффекты (EFFECTS, FILTERS, apply_effects) | effects.py |
| Roto Brush: процесс SAM 2.1 (.venv-roto) | roto_worker.py |
| Roto Brush: маски, уточнение края, наложение на кадр видео | matte.py (вызов в render.draw_layer) |
| Roto Brush: окно | ui/roto_panel.py |
| 3D-трекер: процесс OpenCV+SciPy (.venv-track) | track_worker.py |
| 3D-трекер: сцена, камера SFX, экспорт JSX | camtrack.py |
| 3D-трекер: окно | ui/tracker_panel.py |
| Sapphire-подобные эффекты, партия 2 (grunge, …) — дописывают EFFECTS/FILTERS | sapphire.py |
| Переходы (эффекты категории transition, окно, партнёр A, T(A,B,p)) | transitions.py (вызов в render.render_frame, окно на таймлайне — ui/timeline._paint_transition) |
| Время (Posterize Time, Echo) + Deep Glow, Motion Tile | fx_extra.py (вызов в render._composited) |
| Фигуры / операторы | shapes.py, shape_ops.py |
| Медиа, прокси | media.py, proxy.py |
| Звук | audio.py |
| Кэш / фоновый рендер | cache.py, render_service.py, render_worker.py, gpu.py |
| Экспорт | export.py |
| Плагины (API, загрузка, реестр) | plugins.py; пример — ../plugins_examples/rgb_scanlines.py |
| Переводы EN | i18n.py (каждая новая tr()-строка → EN) |
| Окно, меню, хоткеи, рабочие среды | ui/main_window.py |
| Таймлайн / граф | ui/timeline.py |
| Вьювер | ui/viewer.py |
| Effect Controls | ui/effect_controls.py |
| Правые панели, Flow | ui/right_panels.py, ui/flow.py |
Тесты: `tests/test_*.py` (offscreen), всё сразу — `tests/run_all.sh`. Вывод картинок — /tmp/sfx-tests.
