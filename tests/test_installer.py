# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Автоустановка движков: без движка кнопка активна, по нажатию — вопрос, установка скриптом, затем запуск.
Настоящие install_*.sh не запускаются — вместо них фальшивый скрипт."""
import os, sys, time, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from sfx.i18n import tr
from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication([])
from sfx.editor import Editor
import sfx.ui.installer as inst
import sfx.ui.tracker_panel as tp
import sfx.ui.roto_panel as rp

d = tempfile.mkdtemp(prefix="sfx-inst-"); marker = os.path.join(d, "installed")
script = os.path.join(d, "install.sh")
state = {"exit": 0, "asked": 0}
def write_script():
    with open(script, "w") as f:
        f.write(f"echo '== 1/2 шаг'\nprintf 'загрузка 50%%\\r загрузка 100%%\\n'\n"
                f"[ {state['exit']} -eq 0 ] && touch {marker}\nexit {state['exit']}\n")
def ask(*_a, **_k):
    state["asked"] += 1; return QMessageBox.StandardButton.Yes
inst.QMessageBox.question = ask
real = inst.offer_install
tp.offer_install = rp.offer_install = lambda parent, _s, *a: real(parent, script, *a)   # абсолютный путь побеждает ROOT
tp.tracker_available = rp.roto_available = lambda: os.path.exists(marker)
def wait(cond, sec=20):
    t = time.time()
    while not cond():
        app.processEvents(); time.sleep(0.01)
        if time.time() - t > sec: raise TimeoutError

ed = Editor(); ed.set_idle_cache(False); ed.proxies.enabled = False
# 1) нет движка — кнопки активны, подсказка без жёсткого пути ~/sfx
panel = tp.TrackerPanel(ed); roto = rp.RotoPanel(ed)
assert panel.btn_run.isEnabled() and roto.btn_start.isEnabled()
assert "~/sfx" not in panel.info.text() and "~/sfx" not in roto.status.text(), panel.info.text()
# 2) установка упала — кнопка снова активна, сказано, что не удалось, движок не запускался
state["exit"] = 1; write_script(); panel.run()
assert state["asked"] == 1 and not panel.btn_run.isEnabled()
wait(lambda: panel.btn_run.isEnabled())
assert panel.info.text().startswith(tr("Установка не удалась. Запустите в терминале, чтобы увидеть ошибку: ")), panel.info.text()
# 3) установка прошла — движок сразу запускается (без слоя он просит выбрать видеослой)
state["exit"] = 0; write_script(); panel.run()
wait(lambda: panel.btn_run.isEnabled())
assert panel.info.text() == tr("Выберите видеослой на таймлайне."), panel.info.text()
# 4) движок есть — вопроса больше нет
asked = state["asked"]; panel.run(); roto.start()
assert state["asked"] == asked
if i18n.LANG == "en":
    for s in ("Установка", "Установка: ", "Установка завершена.", "Установка… это может занять несколько минут.",
              "Движок не установлен. Скачать и установить сейчас? Загрузка: {}.",
              "Установка не удалась. Запустите в терминале, чтобы увидеть ошибку: "):
        assert tr(s) != s, s
print("OK installer")
sys.stdout.flush(); os._exit(0)
