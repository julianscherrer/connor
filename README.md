# Connor 🤖✍️

**Dein persönlicher KI-Kumpel – läuft zu 100 % lokal auf deinem PC.**
Keine Cloud, keine Datencenter, keine fremden Server. Deine Chats, Geschichten und Erinnerungen bleiben bei dir.

Connor hilft dir beim Schreiben von Geschichten, spinnt mit dir Charaktere, Welten und Plots, schreibt weiter, wenn du feststeckst, merkt sich alles Wichtige und erinnert dich an Termine.

---

## Was Connor kann

| | |
|---|---|
| 💬 **Chat** | Locker reden wie mit einem guten Freund – auf Deutsch (Schweiz), per «du». Antworten erscheinen live. |
| ✍️ **Schreibwerkstatt** | Deine Geschichten in einem eigenen Editor. Ein Klick, und Connor **schreibt weiter**, liefert **Ideen** oder gibt **ehrliches Feedback**. Du entscheidest, was übernommen wird. |
| 🧠 **Gedächtnis** | Connor merkt sich Charaktere, Orte, Welten, Plot-Ideen und deine Vorlieben – sortiert nach Projekt und Status (Kanon, Entwurf, offen, verworfen). Passende Einträge holt er automatisch hervor, wenn ihr darüber redet. |
| ⏰ **Erinnerungen** | «Erinnere mich morgen um 18 Uhr an Kapitel 3» – Connor meldet sich pünktlich, auch minimiert im Tray. Auch wiederholend (täglich/wöchentlich). |
| 🔎 **Websuche** | Sucht im Internet, liest Seiten und fasst mit Quellen zusammen. Abschaltbar. |
| 💾 **Chat-Archiv** | Alle Chats werden gespeichert, sind durchsuchbar und als Markdown exportierbar. |

---

## So bleibt alles bei dir 🔒

- **Das KI-Gehirn ist [Ollama](https://ollama.com)** – ein Programm, das KI-Modelle direkt auf deinem PC ausführt.
- **Alle Daten liegen im Ordner `data/`** (Datei `connor.db`). Für ein Backup kopierst du einfach diesen Ordner.
- **Der Ordner `data/` wird nie auf GitHub hochgeladen** (steht in `.gitignore`). Auf GitHub landet nur der Programmcode.
- **Einzige Ausnahme ist die Websuche:** Wenn Connor im Internet sucht, gehen *nur die Suchbegriffe* an DuckDuckGo bzw. Wikipedia – nie deine Chats oder Geschichten. Du kannst die Websuche in den Einstellungen ausschalten, vor jeder Anfrage nachfragen lassen oder eine eigene [SearXNG](https://docs.searxng.org)-Instanz verwenden.

---

## Installation auf deinem PC (Windows)

### 1. Python installieren
Lade Python 3.10 oder neuer von [python.org/downloads](https://www.python.org/downloads/) herunter.
**Wichtig:** Beim Installieren den Haken bei **«Add python.exe to PATH»** setzen.

*Oder im Terminal (PowerShell):* `winget install -e --id Python.Python.3.12`

### 2. Ollama installieren
Lade Ollama von [ollama.com/download](https://ollama.com/download) herunter und installiere es.

*Oder:* `winget install -e --id Ollama.Ollama`

### 3. Ein KI-Modell laden
Öffne ein Terminal (PowerShell) und tippe zum Beispiel:

```powershell
ollama pull qwen3:8b
```

Welches Modell passt zu deinem PC?

| Dein PC | Empfehlung | Download |
|---|---|---|
| 8 GB RAM, keine starke Grafikkarte | `qwen3:4b` | ca. 2.5 GB |
| 16 GB RAM oder Grafikkarte mit 8 GB | **`qwen3:8b`** (Empfehlung) | ca. 5 GB |
| Grafikkarte mit 12–16 GB | `qwen3:14b` | ca. 9 GB |

Andere Modelle gehen auch (z. B. `llama3.1:8b`, `mistral-nemo`). Damit Connor **selbst** speichern, erinnern und suchen kann, braucht das Modell «Tools»-Unterstützung – eine Liste findest du auf [ollama.com/search?c=tools](https://ollama.com/search?c=tools). Ohne Tools funktioniert alles trotzdem, dann über Befehle wie `/merke` (siehe unten).

### 4. Connor holen
```powershell
git clone https://github.com/DEIN-NAME/connor.git
cd connor
```
*(Ohne Git: Auf GitHub «Code → Download ZIP» und entpacken.)*

### 5. Starten
**Doppelklick auf `start.bat`.** Beim ersten Mal richtet sich Connor selbst ein (ein paar Minuten), danach startet er in Sekunden.

> Linux/macOS: `./start.sh`

---

## Von diesem PC auf GitHub bringen (einmalig)

1. Auf [github.com/new](https://github.com/new) ein neues Repository anlegen – z. B. `connor`, am besten **Private**, **ohne** README.
2. Im Connor-Ordner im Terminal:
   ```powershell
   git remote add origin https://github.com/DEIN-NAME/connor.git
   git push -u origin main
   ```
3. Auf deinem privaten PC weiter bei [Installation → Schritt 4](#4-connor-holen).

---

## Bedienung

### Chat
- **Enter** sendet, **Shift+Enter** macht eine neue Zeile. **■ Stopp** bricht eine Antwort ab.
- Oben rechts im Chat wählst du ein **Projekt** (z. B. «Drachenreiter»). Connor ordnet dann neue Erinnerungen diesem Projekt zu und holt passende Fakten hervor.
- Die Knöpfe über dem Eingabefeld (✍️ Weiterschreiben, 💡 Ideen, 🧙 Charakter …) geben dir einen Startsatz vor.
- Rechtsklick auf einen Chat links: umbenennen, als Markdown exportieren, löschen.

Mit einem Tools-Modell sagst du einfach ganz normal:
> «Merk dir: Lira ist eine Elfe und hat Angst vor Wasser.»
> «Erinnere mich morgen um 18 Uhr ans Kapitel.»
> «Such mal, wie ein mittelalterlicher Langbogen gebaut wurde.»

### Befehle (funktionieren mit jedem Modell)

| Befehl | Beispiel |
|---|---|
| `/merke Titel: Inhalt` | `/merke Lira: Elfe, 19, hat Angst vor Wasser` |
| `/erinnere <wann> <was>` | `/erinnere morgen 18:00 Mama anrufen` · `/erinnere Freitag 19 Uhr Kapitel 4` · `/erinnere in 2 Stunden Pause` |
| `/suche <Begriff>` | `/suche Geschichte der Wikinger` |
| `/hilfe` | zeigt alle Befehle |

### Schreibwerkstatt
Links deine Geschichten, rechts der Editor (speichert automatisch). Unten:
- **✨ Weiterschreiben** – Connor schreibt im gleichen Stil weiter (Länge wählbar). Mit **✓ Übernehmen** landet der Text in deiner Geschichte – *Ctrl+Z* macht's rückgängig.
- **💡 Ideen** – mehrere Richtungen, wie es weitergehen könnte. **🧠 Als Idee merken** speichert sie im Gedächtnis.
- **🔎 Feedback** – ehrliche Rückmeldung zu Tempo, Figuren, Dialogen und Logik.
- Im Feld «Wunsch an Connor» kannst du die Richtung vorgeben, z. B. *mehr Spannung* oder *Dialog zwischen Lira und Tom*.

### Gedächtnis
Hier siehst und bearbeitest du alles, was Connor weiss. Filtern nach Projekt und Kategorie, Doppelklick zum Bearbeiten. **Kanon** = steht fest, **Entwurf** = Idee in Arbeit, **offen** = ungeklärt, **verworfen** = nicht mehr verwenden.

### Erinnerungen
Einfach eintippen: *«morgen 18:00 Mama anrufen»*, *«in 2 Stunden Pause»*, *«20.11. 9 Uhr Zahnarzt»*. Oder **📅 Mit Datum wählen**. Connor meldet sich mit Benachrichtigung und Popup («In 10 Minuten nochmal» oder «Erledigt»).

> Erinnerungen funktionieren, solange Connor läuft. Beim Schliessen des Fensters läuft er im Tray (unten rechts) weiter. Tipp: In den Einstellungen **«Connor mit Windows starten»** aktivieren. Verpasste Erinnerungen zeigt er beim nächsten Start.

### Einstellungen (⚙ oben rechts)
Modell, Kreativität, Kontextgrösse, Denkmodus, dein Name, Extra-Wünsche an Connors Persönlichkeit, Websuche, Tray und Autostart.

---

## Update und Backup

- **Update:** Doppelklick auf `update.bat` (holt die neuste Version von GitHub). Deine Daten bleiben unverändert.
- **Backup:** Den Ordner `data/` kopieren. Zum Wiederherstellen zurückkopieren.
- **Auf einen anderen PC umziehen:** Connor dort installieren und den `data/`-Ordner hineinkopieren.

---

## Probleme?

| Problem | Lösung |
|---|---|
| «Ollama läuft nicht» | Ollama starten (Startmenü) oder installieren. Connor versucht es beim Start auch selbst. Danach oben rechts auf ↻ klicken. |
| «Kein KI-Modell installiert» | Im Terminal `ollama pull qwen3:8b` und dann ↻ klicken. |
| Antworten sind sehr langsam | Kleineres Modell wählen (`qwen3:4b`), Denkmodus auf «Aus» lassen, Kontextgrösse verkleinern. |
| Connor speichert nichts von selbst | Das Modell kann keine Tools → anderes Modell wählen oder `/merke` benutzen. |
| Websuche klappt nicht | Internet prüfen. DuckDuckGo blockiert manchmal kurz – Connor weicht dann auf Wikipedia aus. |
| Fenster verschwunden | Connor läuft im Tray (unten rechts, evtl. unter «^»). Klick aufs Icon. |
| Etwas anderes | In `data/connor.log` steht, was passiert ist. Zum Debuggen im Terminal starten: `.venv\Scripts\python.exe main.py` |

---

## Für Bastler

```
connor/
  engine.py       Gesprächslogik: Prompt bauen, Ollama fragen, Werkzeuge ausführen
  prompts.py      Connors Persönlichkeit (System-Prompt) und Schreib-Vorlagen
  tools.py        Werkzeuge: Gedächtnis, Erinnerungen, Websuche
  database.py     SQLite: Chats, Gedächtnis (mit Volltextsuche), Erinnerungen, Geschichten
  llm.py          Ollama-Anbindung (Streaming, Tools, Abbrechen)
  websearch.py    DuckDuckGo / Wikipedia / SearXNG + Webseiten lesen
  timeparse.py    versteht «morgen um 18 Uhr», «in 2 Stunden», «Freitag abends»
  gui/            das Fenster (PySide6 / Qt)
tests/            automatische Tests (mit einem simulierten Ollama)
```

Connors Persönlichkeit steht in `connor/prompts.py` (`PERSONA`) – dort kannst du sie anpassen. Für kleine Wünsche reicht das Feld «Extra-Wünsche» in den Einstellungen.

Tests ausführen:
```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```
