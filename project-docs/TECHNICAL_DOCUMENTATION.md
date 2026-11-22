# SFX (Sound & Special Effects): техническая документация

> Составлено 2026-11-18 по исходникам (`~/sfx`). Если факт выведен, а не проверен, это сказано рядом.
> Размеры файлов — число строк на момент составления.

## 1. Сервер, API, база данных

- **Сервера, сетевых API, endpoints и БД нет.** Это локальное настольное приложение.
- **Данные проекта:** файл `.skl` — это JSON (см. §4).
- **Состояние между запусками:** QSettings, кэш на диске, пресеты Flow, плагины.
- **Межпроцессное взаимодействие:** три вида внешних процессов (§7).
  - фоновый рендер — бинарный протокол через pipe;
  - Roto Brush — строки JSON через stdin/stdout;
  - 3D-трекер — JSON-аргумент на входе, строки JSON на выходе.

## 2. Переменные окружения (только имена)

| Имя | Где | Назначение |
|---|---|---|
| `SFX_WORKERS` | render_service.py | число фоновых процессов превью; `0` — без них |
| `SFX_GPU` | gpu.py | `0` — не использовать OpenGL-блюр |
| `SFX_DECODE_THREADS` | media.py, render_service.py | потоки декодирования в воркерах |
| `SFX_PLUGINS` | plugins.py | другая папка плагинов |
| `SFX_KEYLOG` | ui/main_window.py | путь журнала нажатий клавиш (отладка раскладок) |
| `XDG_CACHE_HOME` | proxy, roto, camtrack | корень кэша (по умолчанию `~/.cache`) |
| `XDG_CONFIG_HOME` | plugins, flow | корень конфигурации (по умолчанию `~/.config`) |
| `QT_QPA_PLATFORM` | тесты, воркеры | `offscreen` для работы без экрана |

Секретов, токенов и ключей API в проекте нет.

## 3. Конфигурация

- **QSettings** `("sfx", "sfx")`. Ключи:
  - `lang` — ru или en;
  - `workspaces` — рабочие среды пользователя;
  - `render` — последние настройки рендера;
  - `plugins_disabled` — id плагинов через запятую.
- **Пресеты Flow:** `$XDG_CONFIG_HOME/sfx/flow_presets.json`.
- **Плагины:** `$XDG_CONFIG_HOME/sfx/plugins/*.py` или папка с `__init__.py`.
- **Ярлык:** `~/.local/share/applications/sfx.desktop`.
  - `Exec=~/sfx/run.sh %F`
  - `Icon=sfx-syntora`
  - `StartupWMClass=sfx`
  - `Name=SFX`, `GenericName=Sound & Special Effects — видеоредактор и эффекты`
- **main.py:**
  - `app.setDesktopFileName("sfx")` — связь окна с ярлыком;
  - иконка окна берётся из `~/sfx/sfx.png`, **которого сейчас нет** (см. CURRENT_STATE).
- **Окружения Python:**
  - `.venv` (`requirements.txt`) — основное;
  - `.venv-roto` (`install_roto.sh`) — Roto Brush;
  - `.venv-track` (`install_tracker.sh`) — трекер;
  - `run.sh` сам создаёт `.venv` при первом запуске.

## 4. Модель данных (`sfx/model.py`, 734 строки)

- `Project`: `comps: list[Composition]`, `media: list[str]`, `active` (id композиции).
  - `to_dict()` = `{"version": 2, "media": […], "comps": […], "active": id}`.
  - Есть чтение проектов версии 1: ключ `"comp"`.
- `Composition`: `id`, `name`, `width`, `height`, `fps`, `duration` (в кадрах), `work_in/work_out`, `motion_blur`, фон, `layers`.
  - Сериализуются все поля, кроме приватных `_…`.
  - `active_camera(frame)` — верхняя видимая камера.
- `Layer(kind, name, width, height, comp, source=…)`. **kind:** `video`, `image`, `audio`, `text`, `solid`, `shape`, `camera`, `null`, `comp` (прекомп). Поля:
  - `id` (12 hex), `start`, `in_point`, `out_point` — кадры композиции;
  - флаги: `visible`, `solo`, `locked`, `motion_blur`, `adjustment`, `fx_on`, `threed`, `reverse`, `frame_blend`;
  - `blend` — режим наложения; `matte` = `{"layer": id, "mode": alpha|alpha_inv|luma|luma_inv}`;
  - `label`, `parent`;
  - текст: `text`, `font_family`, `font_size`, `bold`, `color`;
  - `effects: list[dict]` — каждый `{"id", "type", "on", "name"?, "opts": {…}}`;
  - `props: dict[str, Property]`, а также маски и фигуры.
- **Ключи свойств:**
  - трансформация: `anchor`, `position`, `scale`, `rotation`, `opacity`;
  - 3D: `orientation`, `rot_x`, `rot_y`;
  - камера: `poi`, `zoom`;
  - звук: `audio_levels`;
  - видео: `speed`;
  - эффекты: `fx/<id эффекта>/<параметр>`;
  - маски: `mask/<id>/<k>`;
  - фигуры: `shape/<sid>/<k>`, операторы фигур: `shape/<sid>/op_<oid>_<param>`.
- `Property`: `value` (список чисел), `keyframes: list[Keyframe]`, `expr`, `expr_on`.
  - `value_at(frame)` учитывает выражение;
  - `raw_at` — без выражения;
  - `set_value_at`, `add_keyframe`, `toggle_stopwatch`.
- `Keyframe(frame, value, out_x, out_y, in_x, in_y, hold)` — dataclass. Время ключа **относительно `layer.start`** (локальный кадр).
- `Layer.local(comp_frame)` — локальный кадр.
- `Layer.src_time(local_frame, fps)` — секунды исходника с учётом скорости, рампов и реверса.
- `timecode(frame, fps)` — запись времени в стиле AE.

## 5. Редактор (`sfx/editor.py`, 1964 строки)

- `Editor(QObject)`: `project`, `comp`, `time`, `sel_layers`, `sel_keys`, `sel_effect`, `pool` (MediaPool), `cache` (FrameCache), `proxies`.
- **Сигналы:** `changed`, `selection_changed`, `time_changed(int)` и другие.
- **Undo/redo:** JSON-снимки `project.comps_dict()`. `snapshot(tag)` склеивает одинаковые теги подряд, если они идут быстрее 1 с.
- **Основные методы (выборка):**
  - создание: `add_media_layer`, `add_solid`, `add_text`, `add_null`, `add_camera`, `add_adjustment`, `add_shape_layer`, `add_mask`, `add_shape`, `add_shape_op`;
  - эффекты: `add_effect(type, layers=None)`, `remove_effect`, `reset_effect`, `duplicate_effect`, `move_effect`, `set_effect_opt`, `_param_default`;
  - анимация: `add_keyframes`, `set_ease`, `apply_curve`, `read_curve`, `time_reverse_keys`;
  - слои: `precompose`, `set_speed`, `set_matte`, `set_parent`, `set_3d`;
  - буфер обмена: `copy_selection`, `cut_selection`, `paste`, `duplicate`, `split`, `trim`;
  - превью: `start_render_service`, `preview_image`, `frame_image`, `ram_preview`, `set_idle_cache`, `ensure_proxies`;
  - проект: `save`, `load`, `new_project`;
  - пипетка: `start_color_pick`, `pick_color_at`.
- **`_param_default(prm)`:** точка со значениями ≤ 1 считается долей размера композиции. Например, `[0.5, 0.5]` даёт центр.
- **`notify()`** — сообщить об изменении. Кэш кадров ключуется подписью состояния (`cache_sig`).
  Внешние данные, например маски Roto, сигнатуру **не меняют**, поэтому RotoPanel вызывает `ed.cache.clear()` сам.

## 6. Рендер (`sfx/render.py`, 354 строки; `three.py`, 169 строк)

- `render_frame(comp, frame, pool, scale=1.0, …) -> QImage`. Слои собираются снизу вверх на QPainter.
- **2D-слои:**
  - Если у слоя нет пиксельных эффектов, масок и матта, он рисуется сразу: `draw_layer` с `layer_transform`.
  - Иначе `_layer_buffer` строит буфер **размером с композицию**, затем `effects.apply_effects`, матт и рисование.
- **Корректирующий слой** берёт всё, что под ним, и применяет к этому свои эффекты (`include_transform=True` для тряски).
- **`draw_layer`:**
  - видео берётся из `pool.video_for(source, q).frame_at(src_t)`;
  - есть «случайные склейки» (`random_edit_offset`);
  - есть смешивание кадров `frame_blend`;
  - **хук Roto Brush:** `matte.roto_apply(layer, img, src_t, reader.fps, local)` накладывает альфу на кадр исходника до трансформаций.
- **3D (`three.py`):**
  - `local_matrix` для 3D-слоёв: `T · Rx(ox)·Ry(oy)·Rz(oz) · Rx(rx)·Ry(ry) · Rz(rz) · S · T(−anchor)`;
  - `camera_params` строит базис из позиции и POI (down = +y), затем крен `rotation`;
  - `view_projection` = `K[zoom, центр композиции] · view`;
  - `layer_homography` задаёт QTransform плоскости слоя;
  - глубинная сортировка по `layer_depth`.
- **Оси:** x вправо, y вниз, z вперёд — так же, как в OpenCV и After Effects.

### 6a. Свет, тени, материалы, глубина резкости (`lighting.py`, 2026-11-19)
- **Модель (`model.py`):** слой `kind="light"`: атрибут `light = {"type": spot|point|parallel|ambient, "falloff": none|smooth|inverse_square, "shadows": bool}`, свойства `position`, `poi` (3D) и `LIGHT_OPTS` (`light_intensity`, `light_color` (RGB 0–255, вид «color»), `cone_angle`, `cone_feather`, `light_radius`, `falloff_dist`, `shadow_darkness`, `shadow_diffusion`). 3D-слой: атрибут `material = {"accepts_lights", "casts_shadows", "accepts_shadows"}` и свойства `MATERIAL_OPTS` (`mat_ambient` 100, `mat_diffuse` 50, `mat_spec` 50, `mat_shine` 5, `mat_metal` 100 — значения по умолчанию AE). Камера: `dof` (bool), `focus_dist`, `aperture`, `blur_level`. `Layer.option_keys()` — свойства группы «Параметры камеры/света/материала» (у света — только относящиеся к типу).
- **Рендер:** `render._needs_3d_pass` отправляет освещённый или размытый 3D-слой в попиксельный путь `_composited`; там после эффектов слоя: `lighting.shade()` и `blur(dof_radius)`. Нет ни одного включённого света — 3D-слои не освещаются (как в AE), быстрый путь QPainter не меняется.
- **`shade()`:** пиксели слоя → координаты слоя (обратная гомография `view_projection @ world_matrix`, столбцы 0,1,3) → точка в мире. Нормаль плоскости разворачивается к камере (двусторонние слои). Диффуз: `k_diffuse/0.5 · max(0, n·L) · затухание · конус · (1 − тень)` (Diffuse 50% = полный свет); блик Блинна–Фонга `k_spec · (n·H)^(2 + 200·shine²)`, металл 100% — блик цвета поверхности. Spot: конус `cone_angle`, мягкий край `cone_feather`. Затухание: smooth — полный свет до `light_radius`, линейно до нуля за `falloff_dist`; inverse_square — `min(1, (radius/d)²)`.
- **Тени (`_occlusion`):** луч точка→свет пересекается с плоскостями слоёв «Отбрасывает тени»; в точке пересечения берётся альфа отбрасывающего слоя в его локальных координатах (`_local_alpha` рисует слой через `draw_layer`). Несколько слоёв: `1 − Π(1 − α)`. Рассеивание — размытие карты тени в кадре.
- **DOF (`dof_radius`):** радиус = `aperture · |depth − focus| / depth · blur_level · scale`. Ограничение: один радиус на слой по глубине его центра (shortcut, отмечен в коде).
- **Интерфейс:** меню «Слой → Новый свет» (Ctrl+Alt+Shift+L), ПКМ → Создать → Свет; правая панель «Свойства слоя»: тип света, затухание, «Отбрасывает тени»; у камеры — «Глубина резкости»; у 3D-слоя — «Принимает свет / Отбрасывает тени / Принимает тени». Вьювер рисует выделенный свет (значок + линия к точке интереса).
- **Исключения:** слой-свет исключён везде, где исключена камера (рендер, звук, эффекты, переходы, выравнивание, маски, вьювер, переключатели таймлайна).

## 7. Внешние процессы

### 7.1 Фоновый рендер превью (`render_service.py` 183 стр. ↔ `render_worker.py` 79 стр.)
- `RenderService` (QObject) запускает N процессов: `python -m sfx.render_worker` через QProcess. По умолчанию их 4; `SFX_WORKERS` задаёт число.
- **Протокол.** Запрос: 4 байта длины (`<I`) + pickle кортежа `(job_id, state_hash, state_json, frame, scale, transparent, opts)`.
  - Состояние проекта пересылается только при смене хэша.
  - `opts["proxies"]` выбирает пул с прокси или без.
- **Воркер:**
  - stdout переназначен, чтобы `print` не портил канал;
  - загружает плагины и GPU-блюр (`gpu.enable()`);
  - отвечает заголовком `pickle((job_id, w, h, bpl, ms, err, shm, shm_bpl))`; пиксели — в файле `/dev/shm/sfx-<pid окна>-<pid воркера>-<job>.raw` (тогда `bpl = 0`), иначе следом в канале. Окно читает файл `readinto` прямо в новый QImage и удаляет его; хвосты чистит `_clean_shm` при старте и `stop()` (2026-11-20: через pipe 1080p упиралось в ~42 кадр/с, главный поток тратил ~12 мс на кадр);
  - канал stdout расширен до 1 МБ (`F_SETPIPE_SZ`), воркер работает с `nice 5` (прокси-ffmpeg — без nice: он почти весь на видеокарте, а с nice под нагрузкой строился слишком долго).
- **Очередь.** Вне проигрывания текущий кадр — первый в очереди. При проигрывании (`_prefetch`) кадры идут строго вперёд, а позади курсора выкидываются: срочный текущий кадр обгонял будущие, и воркеры не догоняли курсор. Фоновый кэш (`_idle_feed`) держит в работе не больше `воркеры // 2` кадров.
- Текущий кадр всегда стоит первым в очереди. Пока полный кадр считается, показывается черновик в пол-разрешения.

### 7.2 Roto Brush (`roto_worker.py` 309 стр.; окружение `.venv-roto`)
- **Запуск:** `.venv-roto/bin/python sfx/roto_worker.py` (QProcess из `ui/roto_panel.py`). Первой строкой отвечает `{"ready": true}`.
- **Команды** — одна JSON-строка на команду:
  - `open {session, video, start, end}` — кадры исходника `[start, end)` в `frames/%05d.jpg`;
  - `points {frame, points:[[x,y,1|0],…], replace}` — точки «+/−» на кадре (накапливаются);
  - `mask {frame, png}` — готовая маска кадра (ручная кисть);
  - `clear {frame}`;
  - `propagate {from, to}` — вперёд или назад;
  - `stabilize {strength}`;
  - `quit`.
- **Ответы:** `{"ok":true,…}`, `{"error":…}` (`"no_prompt"` — на стартовом кадре нет выделения), `{"progress":k,"of":n,"stage":frames|track|stabilize,"fps"?}`.
- **Сессия** `~/.cache/sfx/roto/<sha1(layer.id|source)[:12]>/`:
  - `frames/`, `masks/` (PNG 8 бит, мягкая маска = sigmoid(logits));
  - `edits/` (маски ручной кисти), `chunk/` (симлинки кадров текущего куска);
  - `session.json`: `{video, start, end, n, w, h, fps, prompts:{кадр:{points|mask}}}`.
  - Если `open` вызван с тем же видео и диапазоном, сессия продолжается (`"resumed": true`).
- **Куски:**
  - Состояние SAM 2 держит не больше `CHUNK=240` кадров.
  - Кадры загружаются лениво (`async_loading_frames`), видео выгружается на CPU (`offload_video_to_cpu`).
  - Через границу куска передаётся маска последнего кадра (`add_new_mask`), подсказки пользователя в куске повторяются.
- **Модель:** `models/sam2.1_hiera_tiny.pt`, конфигурация `configs/sam2.1/sam2.1_hiera_t.yaml`, CUDA, bfloat16 autocast.
- **`stabilize`:** маска каждого кадра усредняется с соседними. Вес соседа = exp(−|ΔI|/0.03)·strength, где ΔI считается по яркости в 1/4 разрешения: где есть движение, усреднения нет.

### 7.3 3D-трекер (`track_worker.py` 468 стр.; окружение `.venv-track`)
- **Запуск:** `.venv-track/bin/python sfx/track_worker.py '<json>'`.
  - Обязательные поля: `video`, `out`, `start`, `end`.
  - Необязательные: `track_w` (ширина трекинга, по умолчанию 960), `corners` (700), `min_dist` (10), `err_px` (2.0), `focal_px`.
- **Выход:** строки JSON `{"progress","of","stage": decode|track|focal|solve|ba}`, затем `{"ok":true, rms_px, solved, n, points, f, fov, seconds, init, h_ratio, screen_locked}` или `{"error": "parallax"|"too_short"|…}`.
- **Этапы:**
  1. `decode` — серые кадры, уменьшенные до `track_w`.
  2. `track_points` — Shi-Tomasi (`goodFeaturesToTrack`) и пирамидальный Лукас–Канаде (окно 21, 4 уровня). Проверка вперёд-назад < 0.7 px, RANSAC по F-матрице между соседними кадрами, пополнение точек.
  3. `drop_screen_locked` — выбрасывает треки, стоящие на месте экрана, пока сцена движется: HUD, логотипы, титры.
  4. `estimate_focal` — критерий Мендонсы–Чиполлы по парам кадров с разносом 15/25/40. Стоимость каждой пары нормирована и взвешена числом инлайеров, по сетке 300 значений фокуса. Вариант выбран сравнением 9 вариантов на 3 наборах данных.
  5. `solve` — стартовая пара с наибольшим числом хороших триангуляций (с поправкой на долю гомографии), E-матрица (`findEssentialMat` → `recoverPose`), триангуляция. Затем PnP RANSAC + `solvePnPRefineLM` для каждого кадра и триангуляция новых точек с самым дальним решённым кадром. Промежуточный BA каждые 30 кадров.
  6. **Итоговый BA:** `scipy.optimize.least_squares` (trf, функция потерь Хьюбера f_scale=1.5, разреженный якобиан, уточняется и фокус). Затем отсев точек с RMS > `err_px` и до 3 повторов.
- **Результат** — `<out>/solution.json`:
  - `version`, `video`, `start`, `n`, `fps`, `w`, `h`, `track_w`, `scale`;
  - `f` (px полного кадра), `fov_h`;
  - `cams[i] = {"R": 9 чисел мир→камера, "C": центр} | null`;
  - `points[] = {"id", "X", "err"}`, `rms_px`, `frame_rms_px`, `solved`, `nodal` (всегда false), `seconds`, `init`, `h_ratio`, `screen_locked`.
  - Отдельно `<out>/obs.json` — 2D-наблюдения решённых точек по кадрам: `[[id, x, y], …]` в пикселях полного кадра.
- **Папка результата:** `~/.cache/sfx/camtrack/<sha1(source|f0|f1)[:12]>/`.

## 8. Эффекты

### 8.1 Реестр (`effects.py` 1427 стр.)
- **`Param(key, ru, en, default, step, suffix, lo, hi, kind, choices, group)`:**
  - `kind`: float, angle, checkbox, choice, color, point;
  - `group = (ru, en)` — подгруппа в Effect Controls.
- **`EffectDef(type, ru, en, params, opts, transform, category, audio, control)`.**
  - **`transform=True`:** эффект не идёт через пиксельный путь и не получает «Непрозрачность эффекта». Так сделаны тряска, Wiggle и **Roto Brush**.
- **Категории** (`CATEGORIES`): blur, stylize, color, distort, generate, keying, sapphire, control, audio (+ plugins).
- **Всем визуальным эффектам автоматически добавляется параметр `mix` («Непрозрачность эффекта»).**
  **Ключ `mix` в своих параметрах использовать нельзя.** Был баг: «Mix With Source» у S_Blur назывался `mix`, и эффект целиком отключался. Теперь он называется `srcmix`.
- **Кадр:** `numpy float32 (H, W, 4)`, каналы **B, G, R, A** (порядок QImage ARGB32), **premultiplied**, диапазон 0..255.
- **Размеры:** параметры заданы в пикселях композиции. В превью их умножают на `scale`.
- **Точки:** хранятся в пикселях композиции.
- **`values(layer, fx, local_frame)`** → dict: число, список (цвет/точка), int (флажок/список).
- **`FILTERS[type](arr, v, scale, frame, fps, seed) -> arr`** — единая сигнатура пиксельных фильтров. `apply_effects` вызывает их в цикле и смешивает результат по `mix`.
- **Помощники:**
  - `_grid(h, w)` — кэш сетки координат;
  - `_remap(arr, sx, sy, nearest)` — выборка с отражением за краем; **карты координат должны быть того же размера, что и кадр**;
  - `blur` (пирамида + гаусс), `_box`, `_resize`, `_downsample`, `_sobel`, `to_array` / `to_image`, `transform_array`;
  - шум для тряски: `_hash`, `_fbm`, `_perlin1`, `_shake_fbm`.
- **Конец файла** — `from . import sapphire` (регистрация второй партии эффектов).

### 8.2 Полный список (68)
- **blur:** blur, dblur, sharpen.
- **stylize:** glow, chroma, mosaic, vignette, threshold, posterize, findedges, emboss, glitch, noise.
- **distort:** shake (S_Shake), mirror, wiggle (Wiggle Transform).
- **color:** bc, hsl, tint, levels, exposure, bw, vibrance, photofilter, colorbal, curves, invert.
- **generate:** flash, fill, ramp (Gradient Ramp).
- **keying:** keylight, **roto** (Roto Brush).
- **control:** slider, anglec, checkc, colorc, pointc (контролы для выражений).
- **audio:** eq, passf, basstreble, comp, reverb, delay, pitch, denoise, balance (через ffmpeg). У reverb и delay свой параметр `mix` (Dry/Wet): это нормально, звук обрабатывается отдельно.
- **sapphire (22):**
  - первая партия в effects.py: blurmo, puddle, distchroma, randedits;
  - вторая партия в **sapphire.py** (1927 стр.): grunge, warpwaves, warpvortex, bokeh, rays, glint, hotspots, halftone, halftonecolor, digitaldamage, filmdamage, jpegdamage, tvdamage, sglow, sblur, sdistort, edgerays, lensflare.

### 8.3 sapphire.py — устройство
- Новые эффекты добавляются в список `NEW_EFFECTS` и в `FILTERS.update({...})` в конце файла. Цикл в конце добавляет им `mix`.
- **Общие функции:**
  - `warp_sample` — режимы Wrap X/Y: Black, Repeat, Reflect, Clamp; Filter;
  - `_zoom_coords` (Z Dist), `atmosphere()` («пыльный воздух», общий для S_Glow, S_EdgeRays, S_LensFlare);
  - `combine()` — режимы Mult, Add, Screen, Difference, Overlay; `_threshold_src`, `hue_rotate`, `_noise_tex`;
  - `radial_streaks` / `radial_rays` — лучи за O(log n) проходов QPainter строго с весом 0.5 (иначе «двоение»), затухание суммой длин L, L/3, L/9;
  - `star_streaks` (Glint), `_down_factor` (длинные лучи считаются в уменьшенном кадре), `glow_image` (сумма 5 гауссов, `_GLOW_UNIT = 0.06` ширины кадра на 1.0).
- **Ограничение:** единица ширины Sapphire не документирована и подобрана на глаз.
- **Источники параметров:**
  - Grunge…TVDamage — панели Effect Controls из видео «I Tested EVERY Sapphire Effect… PART 2» (YouTube tXIUZxvmWbo);
  - S_Glow, S_Blur, S_Distort, S_EdgeRays, S_LensFlare — официальная документация `https://borisfx.com/documentation/sapphire/ae/<name>/`.

### 8.4 Тряска S_Shake (переписана 2026-11-18)
- **Параметры:** Style (`mode`), **Shake Amount** (`master`, общий множитель; 0 — кадр неподвижен), Amplitude (`amount`, px), Position X/Y (`xmul`/`ymul`, %), Rotation (`rot`), Zoom (`zoom`, %, по умолчанию 0), Motion Frequency (`freq`), Smoothness (`smooth`), Randomness (`random`), Motion Blur (`mblur`), **Zoom Compensation** (`zcomp`, %, по умолчанию 0, не зависит от Amount), Direction и Detail (в Advanced).
- **Алгоритм:**
  - 1D-шум Перлина с квинтическим сглаживанием и fBm (Smoothness убирает вес верхних октав);
  - самая быстрая октава ≤ 12 Гц (без скачков на 24–30 к/с);
  - Randomness — «плавающий» темп и неровная огибающая;
  - Impact — затухающие толчки.
- **Номера стилей 0–5 менять нельзя** (на них ссылаются пресеты): 0 Organic, 1 Handheld, 2 Impact, 3 Jitter, 4 Earthquake, 5 Directional. Новые: 6 Subtle, 7 Cinematic, 8 Heavy.
- **Удалено:** авто-зум «Скрывать края», который был включён по умолчанию и давал «Amount как зум». Старый ключ `autozoom` больше не читается.

### 8.5 Roto Brush в рендере (`matte.py` 164 стр.)
- `roto_apply(layer, img, src_t, video_fps, local_frame)`:
  - индекс кадра сессии = round(src_t·fps) − start;
  - маска уточняется функцией `refine()`: guided filter по яркости кадра (`refine` px, `detail` → eps = 10^(−1−3·detail)), затем Choke / Shift Edge / Contrast (сдвиг центра и усиление вокруг 0.5), Feather (3×box ≈ гаусс), Invert;
  - вид: 0 Result, 1 Alpha, 2 Overlay.
- Вне отслеженного диапазона и без маски кадр не меняется.
- Кэш масок — LRU на 48 по (путь, mtime). Работает и в фоновых процессах рендера.

### 8.5a Переходы (`transitions.py`)
- Переход = эффект категории `transition` (`EffectDef.transition=True`) на входящем слое B; общие параметры группы «Время/Timing»: `dur` (кадры), `align` (0 центр, 1 начало, 2 конец на стыке), `ease`.
- `has_pixel_effects` переходы не считает; `apply_effects` их пропускает — рисует `render_frame`.
- `transitions_at(comp, frame)` → `{B.id: (A|None, p, fx, v)}`; функция перехода `f(a, b, p, v, scale, frame, fps, seed)`, a и b — BGRA premultiplied уже с непрозрачностью слоёв. `run_filter(type, arr, over, …)` вызывает любой фильтр из FILTERS с параметрами по умолчанию (точки-доли → пиксели).
- B вне In/Out: видео показывает кадр по `src_time` (до Start — первый кадр); A после Out — продолжение исходника.

### 8.5b Время и fx_extra (`fx_extra.py`, `model.py`, `editor.py`)
- `time_remap` — свойство слоя (секунды исходника, ключи в локальных кадрах). `Layer.src_time` берёт его вместо скорости/реверса и ограничивает длиной исходника. `local_end` при remap = None (слой тянется).
- `Editor.toggle_time_remap()` — ключи на In/Out; повтор удаляет. `Editor.freeze_frame(hold=N)` — ключ-удержание на курсоре и ключ через N кадров, ключи после курсора сдвигаются на N, Out += N; `hold=None` — удержание до конца; `last=True` — последний кадр до конца композиции.
- Эффекты `time=True` (posterizetime, echo): `render._composited` берёт `fx_extra.time_sources(layer, frame, fps)` → список (кадр, вес) и оператор Echo; содержимое и эффекты рисуются на этих кадрах, трансформация — на текущем. `echo_combine` — операторы Add/Max/Min/Screen/Composite In Back/Front/Blend.
- `deepglow` (glow_image из sapphire, Exposure в стопах, порог с мягкостью, ХА через разную ширину каналов, Tint, Aspect/Angle) и `motiontile` (плитка в пространстве кадра композиции) — обычные FILTERS.
- Переходы: параметр `edge` (0 вход, 1 выход). Для выхода `transitions_at` отдаёт A=None, render подставляет слой как A и пустоту как B.

### 8.6 3D-трекер в сцене (`camtrack.py` 300 стр.)
- `Solution(folder)` — `cam(i)`, `quality()`, `set_ground(ids)`, `set_origin(id)`, `set_distance(a, b, d)`, `to_scene`, `cam_scene`, `zoom_for`.
  - Сцена: `Xs = s·Ra·(X − O) + (W/2, H/2, 0)`.
  - Плоскость земли: SVD по точкам, нормаль в сторону камеры → ось −y.
  - Масштаб по умолчанию: медианная глубина ≈ 1000 px.
- `build_sfx_camera` — камера SFX: ключи position / poi / rotation(крен) на каждом кадре слоя, zoom = f·comp_w/video_w·scale.
  - Крен считается под ту же формулу, что `three.camera_params`.
  - **Проверено:** ошибка репроекции через камеру SFX 0.58 px против 0.563 у решения.
- `build_sfx_nulls`, `build_sfx_text_on_plane` (orientation [270, 0, 0] — лицом к камере, текст читается).
- `export_jsx` — скрипт для AE: камера с `NO_AUTO_ORIENT`, ключи Position и Orientation на каждом кадре (`setValuesAtTimes`), Zoom, 3D-нули, облако точек (солиды 6×6 под нулём).
  - Orientation считается как M = Ry(y)·Rx(x)·Rz(z). **Это допущение** выведено из известной формулы выражения `lookAt()` AE. В настоящем AE не проверено.

### 8.7 Монтаж: стоп-кадр, маркеры, slip (2026-11-19)
- **Стоп-кадр с удержанием** (`Editor.freeze_frame(hold=N, ripple=)`): Time Remap с ключом-удержанием; `ripple=True` сдвигает слои, которые начинаются не раньше конца замороженного, на N кадров (ключи в локальном времени едут сами), длина композиции растёт при необходимости. Курсор вне слоя — слой пропускается. Диалог Shift+Alt+F: секунды + галка «Сдвинуть следующие клипы» (QSettings `freeze_ripple`).
- **Звук и Time Remap:** `audio.mix` ведёт слой с `time_remap` через `_mix_retimed` (раньше только скорость/реверс → звук расходился с картинкой после стоп-кадра); там, где время стоит, — тишина.
- **Маркеры композиции:** `Composition.markers = [{"frame", "name"}]` (сериализуется через `__dict__`, старые проекты — пустой список); `Editor.add_marker / remove_marker / set_marker`; таймлайн: флажки на линейке (`_hit("marker")`), перетаскивание с прилипанием, Ctrl+клик — удалить, двойной клик — имя; цели прилипания и J/K включают маркеры; при смене fps маркеры пересчитываются (`panels.CompSettingsDialog.apply`). Хоткей `*` (и Shift+*), меню «Удалить все маркеры».
- **Slip:** `Editor.slip(layer, frames)` — In/Out на месте, `start` сдвигается в пределах исходника, ключи компенсируются (остаются на кадрах композиции). Таймлайн: зона `ghost` (тень исходника) — тянуть.
- **Рабочая область:** `set_work("all")`, двойной клик по полосе; B/N с отменой.
- **Маркеры слоя (2026-11-19, ночь):** `Layer.markers = [{"frame": локальный кадр, "name"}]` — в локальном времени, поэтому едут со слоем (через `start`); старые проекты — пустой список (поле задаётся в `Layer.__init__`, `from_dict` перезаписывает, если есть). `Editor.add_marker(frame, name, layer=None, snapshot=True)`; `add_marker_auto()` — хоткей `*`: выделены слои → маркер на каждом (одна точка отмены), иначе маркер композиции (как в AE). `_marker_list(m)` находит владельца маркера по идентичности объекта, поэтому `remove_marker` / `set_marker(frame=кадр композиции)` работают для обоих видов; `marker_comp_frame(m)`. Таймлайн: `_paint_layer_markers` — флажки внизу полоски слоя, хит того же типа `"marker"` (Ctrl+клик, перетаскивание, двойной клик — общий код). `split` и `lift_work_area` делят маркеры между половинами; смена fps пересчитывает и маркеры слоёв; «Удалить все маркеры» чистит и их; J/K и прилипание их учитывают.
- **Lift / Extract Work Area (2026-11-19, ночь):** `Editor.lift_work_area(extract)` — цели: выделенные слои, иначе все; заблокированные пропускаются. Слой внутри области удаляется (дети отвязываются), через всю область — делится копией (`from_dict(to_dict)` + новый id), частично — обрезается. `extract=True` сдвигает цели с `in_point ≥ work_out` на длину области (`start/in/out`, ключи в локальном времени едут сами). Длина композиции и рабочая область не меняются (как в AE). Меню «Правка», хоткея нет (в AE тоже).
- **Ripple Delete (2026-11-19, ночь):** `Editor.ripple_delete()`, Shift+Del: удаляет выделенные незаблокированные слои, промежуток `[min in, max out)`; все незаблокированные слои с `in_point ≥ max out` сдвигаются влево на его длину. Перекрывающие промежуток слои не двигаются (поведение как в Premiere без sync lock; в AE этой команды нет).

## 9. Интерфейс (`sfx/ui/`)
- **3D-виды (2026-11-19), `three.py`:** `Composition.view3d ∈ VIEWS` (`active` по умолчанию), `view_custom = {yaw, pitch, dist (в зумах по умолчанию), target}`, `view_ortho = {вид: {scale, target}}` — сериализуются (`__dict__`), старые проекты → `active`. `camera_params()` сначала спрашивает `view_override(comp)` — вид действует **только для `project.active`** (вложенный прекомп с видом рендерится через свою камеру). Ортогональ = камера в `ORTHO_D = 1e6` px, `zoom = ORTHO_D·scale` (рендер-код не менялся; перспективная ошибка ~глубина/1e6). `fit_ortho` вписывает 3D-слои/камеры/свет/POI (масштаб ≤ 1) — вызывается при первом переключении на вид (`MainWindow.set_view3d`). Вид входит в JSON проекта → в подпись кэша и в фоновые процессы автоматически. Экспорт (`ExportThread.run`) ставит всем композициям `view3d = "active"`. `lighting.dof_radius` → 0 в видах. Вьювер: `_draw_camera`, `_camera_icon_at`, ветка `("view", mode, …)`, колесо → `zoom_view`, «move» 3D-слоя/камеры/света без родителя — по `basis[0..1]` вида с коэффициентом глубина/zoom.
- **Кольца поворота (2026-11-20), `viewer._rings/_ring_at/_paint_rings`:** ось кольца i = `par[:3,:3] @ frames[i]`, где frames = [O, O·Rx, O·Rx·Ry] (O — Orientation), пары (u, v) с u×v = ось: X→(y,z), Y→(z,x), Z→(x,y) — тогда рост θ = положительный поворот (матрицы `_rx/_ry/_rz` правые). Радиус в мире = `RING_PX` / (px на единицу по осям экрана вида в центре). «Раскрытость» = √(меньшее собственное число ковариации точек); ребром (< 0,2·R) — штраф 6 px при выборе. Угол = (сдвиг · касательная) / `RING_PX` рад.
- **Раскладки видов (2026-11-20), `ui/side_view.py`:** `SideView._swap()` подменяет `comp.view3d/view_ortho/view_custom` на свои на время рендера/навигации и откатывает (ортогональный масштаб и Custom хранятся в панели). `render_frame` в главном потоке, масштаб по размеру панели; `schedule()` — таймер 40 мс, при проигрывании пропуск и остановка таймера (`playing_changed`). `three.layer_outlines` — контуры 3D-слоёв (в px композиции). `MainWindow.set_view_layout(1|2|4)`: сетка `view_grid` (главный (0,0), боковые (0,1)/(1,0)/(1,1)), панели создаются один раз и прячутся.
- **Окно «Проекты» (2026-11-19), `ui/home.py`:** `collect()` = `recent_list()` + `SCAN_DIRS` (`~/Eddits`) + `autosave_dir/*_autosave.skl`, сортировка: существующие по mtime, пропавшие в конце; `ago()`; `add_recent` из `MainWindow.open_path` (кроме автосохранений) и `save`; `HomeDialog.choice`; `MainWindow.show_home(startup)`; `main.py` показывает при запуске, если нет аргументов и проект пуст.
- **Рендер выбранной композиции:** `RenderDialog(comp, …, comps=)` — `comp_box`, `_fill_comp_items`, `_on_comp`; `MainWindow.export_dialog(comp=None)` → `pd["active"] = dlg.comp.id`. ПКМ «Проект» → `project_action("render_comp")`, ПКМ слоя-прекомпа.
- **Effect Controls:** `refresh` запоминает прокрутку при пересборке для того же слоя (по id), `_restore_scroll` по `rangeChanged`.
- **Таймлайн:** полоса рабочей области — хиты `work_in`/`work_out` (±6 px) и `work_bar`; перетаскивание с прилипанием (`_snap_targets`) и ограничениями; тень исходника (`ghost`) — только у выделенного слоя.
- **Новые слои:** `Editor.new_layer_time()` (0 при `layers_at_start`, иначе CTI) — во всех `add_*` без явного места, в `drop_items` и `_import`; вьювер при drop передаёт `at=None`.
- **Гизмо 3D-перемещения (2026-11-19, ночь), `ui/viewer.py`:** `_gizmo()` — для текущего выделенного 3D-слоя / света / камеры (инструмент V, слой активен и не заблокирован): начало = Position в мировых координатах (`world_matrix @ inv(local_matrix)` даёт матрицу родителя; у камеры и света родитель не учитывается), направления осей — проекция шага 100 ед. по X/Y/Z через `three.view_projection`, переведённая в координаты вида; «px на единицу мира» берётся оттуда же. Ось короче 8 px на экране (Z при виде спереди) рисуется по диагонали вверх-вправо с коэффициентом 0,5 px/ед. `_gizmo_axis_at(pos)` — попадание в отрезок 10…70 px от начала (±7 px). Перетаскивание `("axis", слой, ось, точка, Position до, направление, px/ед.)`: сдвиг мыши проецируется на ось, Alt — в 4 раза точнее; значение через `Property.set_value_at` (при включённом секундомере — ключ); одна точка отмены на перетаскивание. Режим осей — World (как World Axis Mode в AE); Local/View-режимов нет. Когда активна сама камера, её начало за ближней плоскостью — гизмо не рисуется (двигать камеру — инструментом C или числами).
- **`main_window.py` (1905 стр.):**
  - меню Файл / Правка / Композиция / Слой / Анимация / Эффект / Вид / Инструменты / Справка / Язык;
  - `_act(menu, text, shortcut, fn, menu_name)` + русские варианты хоткеев (`_ru_variant`);
  - фильтр сканкодов для не-латинских раскладок; рабочие среды;
  - `export_dialog()`, `open_roto()`, `open_tracker()`.
  - раскладка: `_layout_state()` / `_apply_layout_state()` (рабочие среды и последняя раскладка; поле `order` — порядок колонок `top`), `save_last_layout()` при закрытии, `restore_last_layout()` из `main.py` после `show()` (QSettings `last_layout`, `geometry`; ширины ставятся повторно через `QTimer.singleShot(0)`).
- **Окна-инструменты** (Qt.Window, родитель — главное окно):
  - `roto_panel.RotoPanel` — Alt+W;
  - `tracker_panel.TrackerPanel` — Ctrl+Alt+Shift+K.
- **Остальное:**
  - `timeline.py` (2296) — таймлайн и граф-эдитор;
  - `viewer.py` (720) — вьювер и инструменты;
  - `effect_controls.py` (453) — EffectControlsPanel;
  - `widgets.py` (540) — ScrubValue: знаков после запятой 3 при шаге < 0.009, 2 при < 0.05, иначе 1;
  - `flow.py` (453) — Flow, F4;
  - `panels.py` (686), `right_panels.py` (307), `render_dialog.py` (271), `fx_console.py` (119, Ctrl+Space), `icons.py` (138, рисованные иконки), `theme.py` (77, STYLESHEET).
- **Подвох:** у лямбд QAction нужно писать `lambda _=False, k=key: …`, иначе аргумент `checked` перезаписывает `k`.

## 10. Экспорт (`export.py` 332 стр.)
- **Форматы:** mp4, mov, mkv, webm, gif, png (секвенция `имя_%05d.png`).
- **Кодеки:** H.264, H.265, AV1, VP9, ProRes 422, **ProRes 4444** (yuva444p10le с альфой), qtrle, png (rgba).
- По умолчанию h264_nvenc. Кадры BGRA идут в ffmpeg через pipe.
- **Для After Effects** (прозрачный результат Roto): PNG-секвенция или MOV + ProRes 4444 с флажком «Альфа».

## 11. Плагины (`plugins.py` 159 стр., API v1)
- Плагин — файл с `PLUGIN = {"id","name","version","api":1}` и `register(api)`.
- `api.add_effect(name, ru, en, params, fn, category, transform)` — тип становится `"<id>/<name>"`, ему добавляется `mix`.
- `api.add_shape_op(...)`.
- Ошибки ловит `_safe`: кадр не падает, ошибка видна в «Эффект → Плагины…».
- Загрузка в `main.py` и в каждом `render_worker`. Пример: `plugins_examples/rgb_scanlines.py`.

## 12. Зависимости между модулями (кто кого импортирует)
- `main.py` → editor, ui.main_window, ui.theme, plugins, i18n.
- `editor` → model, render, effects, media, cache, proxy, render_service, audio, shapes, i18n.
- `render` → effects (apply_effects, shake_offset…), three, shapes, media; **лениво** → matte (Roto).
- `effects` → i18n; в конце → sapphire (sapphire импортирует из effects: Param, EffectDef, EFFECTS, FILTERS, COMPOSITING, `_grid`, `_remap`, `to_array`, `to_image`, а лениво — blur, `_fbm`, `_box`, `_resize`, `_downsample`, `_sobel`, `transform_array`).
- `matte` → effects (to_array, to_image, values, `_box`).
- `camtrack` → model (Keyframe, Layer), render (update_text_size). Зависимость от three только в тесте.
- `ui/roto_panel` → matte, i18n; QProcess → roto_worker (в `.venv-roto`).
- `ui/tracker_panel` → camtrack, i18n; QProcess → track_worker (в `.venv-track`).
- `roto_worker` и `track_worker` **не импортируют** ничего из SFX и Qt: независимые программы.

## 13. Тесты (`tests/`)
- **Все тесты** — offscreen Qt: `QT_QPA_PLATFORM=offscreen`, `XDG_CACHE_HOME=/tmp/sfx-tests/…`, язык ставится прямым присваиванием `i18n.LANG = "ru"|"en"` (`set_lang` только сохраняет настройку). Картинки пишутся в `/tmp/sfx-tests/`.
- **`run_all.sh`:**
  - flow, bugs, cache, key, filters, idle, three, batch2, ui3, ec, shapes, plugins, sapph2, transitions, time, menus, layout, regress_1119, freeze, markers_slip, lights, fx_audit — каждый на ru и en (`test_menus` идёт ~18 мин: не ставить ему timeout 600);
  - **2026-11-19:** `test_layout` (раскладка окон между запусками), `test_regress_1119` (падение в «Эффектах слоя», Motion Tile, Shift-диапазон, рабочая область), `test_freeze` (стоп-кадр: картинка, ripple, звук, диалог, отмена), `test_markers_slip` (маркеры и slip мышью), `test_lights` (свет/тени/материал/DOF, интерфейс, экспорт), `test_fx_audit` (все эффекты: рендер, параметр, ключи, сохранение, порядок);
  - **2026-11-19 (ночь):** `test_ripple` (Lift/Extract Work Area, Ripple Delete: деление, сдвиг, картинка после стыка, блокировка, родители, отмена, меню, сохранение), `test_gizmo` (гизмо X/Y/Z: только своя координата, сдвиг картинки, Z меняет размер, отмена, ключ, свет); `test_markers_slip` дополнен маркерами слоя; `test_roto` — проверкой маски при «Смешивании кадров»;
  - **2026-11-19 (поздняя ночь):** `test_views` (3D-виды, экспорт через камеру, прекомп, гизмо камеры, F10–F12), `test_regress_1120` (6 задач пользователя на копиях `~/Eddits/*.skl`, оригиналы сверяются по md5);
  - test_roto (ru, en) — если есть `.venv-roto`;
  - test_tracker (ru) — если есть `.venv-track`;
  - test_async.
- **Последний полный прогон (2026-11-20):** 56 из 56 OK (раньше: 2026-11-18 — 30/30).
- **Не входят в run_all:**
  - `test_sapph.py` — первая партия Sapphire;
  - `test_export.py`;
  - `test_ru.py` — проверяет `tr()` и хоткеи на ru; запускался отдельно, выводит «OK».
- **`test_sapph2.py`** — все эффекты второй партии плюс S_*; проверяет, что эффект **меняет кадр**, и пишет время кадра.
- **`test_roto.py`** — панель → SAM 2 → маски → альфа (≥60% кадра вырезано) → картинки overlay/matte.
- **`test_tracker.py`** + `make_synth_track.py`:
  - синтетическое видео 1280×720, 150 кадров, известная камера (f = 900);
  - проверки: ≥95% кадров решено, RMS < 1.5 px, фокус ±5%, камера с ключами, 25 нулей, текст 3D и двигается на экране, JSX корректен.
- Видео для тестов: `~/Videos/Replay_2026-10-31_00-14-28.mp4` (нужно `run_all.sh`).

## 14. Важные технические нюансы (собрано из ошибок)
- **После `ed.undo()` объекты слоёв — новые** (проект восстанавливается из снимка). Ссылки на `Layer`, взятые до отмены, устарели — брать слой заново по id. **Объект композиции тоже новый** — в тестах писать `ed.comp`, а не сохранённое `c = ed.comp`.
- **Тесты с `os._exit(0)`:** сначала `print("OK")`, потом `sys.stdout.flush()`, потом выход — иначе «OK» теряется в pipe и `run_all.sh` пишет FAIL.
- **Фон композиции непрозрачный** — в тестах положение/размер картинки мерить по яркости, а не по альфе.
- **Массивы кадра — BGRA** (`to_array` из ARGB32 little-endian): красный — канал 2.
- **Внешние долгие процессы — через `setpriv --pdeathsig KILL`** (прокси), иначе при падении программы ffmpeg живёт сиротой.
- **Одноразовые QMenu — с `WA_DeleteOnClose`.** `QMenu(self)` — ребёнок окна и без этого живёт до закрытия программы (утечка на каждый ПКМ).
- **deleteLater без цикла событий не срабатывает** (тесты зовут только `processEvents`) — для часто пересобираемых панелей держать прошлую панель и удалять её на следующей пересборке (`EffectControlsPanel._retired`).
- **Свет считается на сетке** `lighting.GRID = 4` px (мелкий слой < 20000 px — попиксельно) и интерполируется: тени на сетке тоже — край тени мягче на ~4 px.
- **Не пересобирать виджеты синхронно из сигналов фокуса.** `QScrollArea.setWidget` удаляет прежний виджет сразу; если это случилось внутри focusOut поля ввода, Qt падает (SIGSEGV). Старое — через `takeWidget()` + `deleteLater()` (см. `EffectControlsPanel.refresh`).
- **Эффекты работают на холсте композиции**, а не в пикселях слоя: эффекты «по границам слоя» (Motion Tile) находят слой по альфе.
1. **Ключ `mix` в параметрах эффекта занят** («Непрозрачность эффекта»).
2. **`_remap` требует карт координат размером с кадр.** Для выборки из текстуры другого размера есть своя функция (`_noise_tex`).
3. **Радиальные и звёздные лучи:** вес прохода строго 0.5, иначе появляются дискретные «копии» (биномиальные веса).
4. **Новые зависимости ставит только пользователь** через `install_*.sh`: у Claude `pip install` заблокирован.
5. **Внешние данные эффекта** (маски Roto) не входят в сигнатуру кэша, поэтому кэш надо чистить вручную.
6. **Масштаб превью.** В JpegDamage была ошибка: scale учитывался дважды, кадр считался в 4× разрешении. Исправлено.
7. **Roto + `frame_blend`:** ~~маска не применялась~~ — исправлено 2026-11-19 (ночь): `roto_apply` вызывается для обоих смешиваемых кадров (`render.draw_layer`).
8. **В трекере режима «только поворот» (nodal) нет.** При малом параллаксе возвращается ошибка `parallax`; описание в `track_worker.py` исправлено 2026-11-19 (ночь).
9. **Стили тряски 0–5** нельзя перенумеровывать.
10. **`add_text()` вставляет слой над выделенным, а не всегда первым.** Новый слой ищите по id, которых не было до вызова.
