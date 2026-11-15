# Connor 🤖✍️

**Dein persönlicher KI-Kumpel – läuft zu 100 % lokal auf deinem PC.**
Keine Cloud, keine Datencenter, keine fremden Server. Deine Chats, Geschichten und Erinnerungen bleiben bei dir.

Connor hilft dir beim Schreiben von Geschichten, spinnt mit dir Charaktere, Welten und Plots, schreibt weiter, wenn du feststeckst, beantwortet Fragen, liest deine Dateien, versteht deine Stimme, merkt sich alles Wichtige und erinnert dich an Termine.

---

## Was Connor kann

| | |
|---|---|
| 💬 **Chat mit zwei Modi** | **💬 Allrounder** für alle Fragen (sachlich, klar, mit Websuche) oder **✍️ Schreibpartner** für Geschichten (kreativ, fantasievoll). Umschalten oben im Chat – pro Chat gespeichert. Locker wie ein guter Freund, auf Deutsch (Schweiz), per «du». |
| ✍️ **Schreibwerkstatt** | Deine Geschichten in einem eigenen Editor. Ein Klick, und Connor **schreibt weiter**, liefert **Ideen** oder gibt **ehrliches Feedback**. Du entscheidest, was übernommen wird. |
| 📚 **Deine Dateien** | Zieh Dateien oder ganze Ordner ins Fenster – **Markdown, Text, Word, PDF, E-Books, Audio**. Connor findet darin die passenden Stellen, wenn ihr darüber redet. |
| 🎤 **Diktieren** | Klicken, sprechen, fertig – die Spracherkennung (Whisper) läuft **lokal**. Auch Sprachmemos und Aufnahmen werden transkribiert. |
| 📄 **Word-Dateien erstellen** | «Mach mir einen Charakterbogen für Lira als Word» – Connor erstellt die Datei, Skripte sogar im echten **Drehbuch-Format**. Jede Antwort hat einen **💾 Word**-Knopf, Geschichten exportierst du in der Schreibwerkstatt. |
| 🧠 **Gedächtnis** | Connor merkt sich Charaktere, Orte, Welten, Plot-Ideen und deine Vorlieben – sortiert nach Projekt und Status (Kanon, Entwurf, offen, verworfen). Passende Einträge holt er automatisch hervor. |
| ⏰ **Erinnerungen** | «Erinnere mich morgen um 18 Uhr an Kapitel 3» – Connor meldet sich pünktlich, auch minimiert im Tray. Auch wiederholend (täglich/wöchentlich). |
| 🔎 **Websuche** | Sucht im Internet, liest die besten Seiten und fasst mit Quellen zusammen. Mit **🌐** erzwingst du die Suche. Abschaltbar. |
| 💾 **Chat-Archiv** | Alle Chats werden gespeichert, sind durchsuchbar und als Markdown exportierbar. |

---

## So bleibt alles bei dir 🔒

- **Das KI-Gehirn ist [Ollama](https://ollama.com)** – ein Programm, das KI-Modelle direkt auf deinem PC ausführt.
- **Alle Daten liegen im Ordner `data/`** (Datei `connor.db`): Chats, Gedächtnis, Geschichten, gelesene Dateien, Erinnerungen. Für ein Backup kopierst du einfach diesen Ordner.
- **Der Ordner `data/` wird nie auf GitHub hochgeladen** (steht in `.gitignore`). Auf GitHub landet nur der Programmcode.
- **Deine Stimme bleibt auf dem PC.** Nur das Sprachmodell wird beim ersten Diktieren einmalig heruntergeladen (in den Ordner `models/`).
- **Einzige Ausnahme ist die Websuche:** Wenn Connor im Internet sucht, gehen *nur die Suchbegriffe* an DuckDuckGo, Bing oder Wikipedia – nie deine Chats, Dateien oder Geschichten. Du kannst die Websuche in den Einstellungen ausschalten, vor jeder Anfrage nachfragen lassen oder eine eigene [SearXNG](https://docs.searxng.org)-Instanz verwenden.

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
**Doppelklick auf `start.bat`.** Beim ersten Mal richtet sich Connor selbst ein (ein paar Minuten, inklusive Spracherkennung ca. 300 MB), danach startet er in Sekunden.

> Linux/macOS: `./start.sh` (für die Spracherkennung zusätzlich `pip install -r requirements-voice.txt`)

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
- **Modus** oben im Chat: **💬 Allrounder** für normale Fragen («Wie funktioniert …?», «Plan mir …», «Übersetz …») oder **✍️ Schreibpartner** für Geschichten. Neue Chats starten im zuletzt gewählten Modus.
- Daneben wählst du ein **Projekt** (z. B. «Drachenreiter»). Connor ordnet dann neue Erinnerungen und Dateien diesem Projekt zu und holt passende Fakten hervor.
- Die Knöpfe über dem Eingabefeld geben dir einen Startsatz vor – je nach Modus andere.
- Rechtsklick auf einen Chat links: umbenennen, als Markdown exportieren, löschen.

**Die Knöpfe neben dem Eingabefeld:**
- **📎 Dateien anhängen** – oder Dateien einfach ins Fenster ziehen. Connor liest sie und nutzt sie in diesem Chat. Grosse Dateien (z. B. ein ganzer Roman) liest er stückweise: Er nimmt jeweils die Stellen, die zu deiner Frage passen.
- **🌐 Websuche** – solange der Knopf leuchtet, sucht Connor bei *jeder* Nachricht zuerst im Internet und nennt die Quellen. Ohne den Knopf entscheidet er selbst, wann er sucht.
- **🎤 Diktieren** – klicken, sprechen, nochmals klicken (⏹). Der Text landet im Eingabefeld. In den Einstellungen kannst du «gleich senden» einschalten.

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
- **🎤 Diktieren** schreibt deinen gesprochenen Text an die Cursor-Position.
- **Datei reinziehen:** eine Word- oder Markdown-Datei kannst du **als Geschichte öffnen** (z. B. dein Manuskript) oder **als Wissen speichern** (z. B. Weltenbau-Notizen). Connor nutzt das Wissen deines Projekts beim Weiterschreiben automatisch.

### Dokumente
Alles, was Connor gelesen hat: Markdown (auch ganze Obsidian-Ordner), Text, Word (.docx), LibreOffice (.odt), PDF, E-Books (.epub), Webseiten (.html) und Audio/Video (.mp3, .m4a, .wav, .mp4 … werden transkribiert). Zieh Dateien oder Ordner einfach ins Fenster oder nutze **＋ Dateien** / **📁 Ordner**. Neue Dateien landen im oben gewählten Projekt. **👁 Anzeigen** zeigt den gelesenen Text, **🏷️ Projekt** ordnet Dateien zu, **🗑️ Entfernen** lässt Connor sie vergessen (deine Originaldateien bleiben unverändert). Liest du eine geänderte Datei erneut ein, ersetzt Connor die alte Version.

> Alte Word-Dateien (.doc) und eingescannte PDFs (nur Bilder) kann Connor nicht lesen – speichere sie als .docx bzw. nutze ein PDF mit Text.

### Dateien erstellen (Word, Markdown, Text)
- **Im Chat einfach sagen:** «Mach mir einen Charakterbogen für Lira als Word», «Schreib Szene 3 als Skript in eine Word-Datei» oder «Speicher das als Word». Connor erstellt die Datei und zeigt im Chat einen Link zum **Öffnen** und **Ordner öffnen**.
- **Skripte** bekommen automatisch ein echtes Drehbuch-Layout: Szenenköpfe (INT. TAVERNE – NACHT), Figurennamen, Regieanweisungen und Dialoge eingerückt, alles in Courier.
- **💾 Word** oben rechts an jeder Antwort speichert genau diese Antwort als Word-Datei – das klappt mit jedem Modell.
- **Schreibwerkstatt:** **💾 Exportieren** speichert die ganze Geschichte als Word, Markdown oder Text.
- Die Dateien landen in **Dokumente\Connor** (änderbar in den Einstellungen). Connor überschreibt nie eine bestehende Datei – er hängt dann «(2)» an.

### Gedächtnis
Hier siehst und bearbeitest du alles, was Connor weiss. Filtern nach Projekt und Kategorie, Doppelklick zum Bearbeiten. **Kanon** = steht fest, **Entwurf** = Idee in Arbeit, **offen** = ungeklärt, **verworfen** = nicht mehr verwenden.

### Erinnerungen
Einfach eintippen: *«morgen 18:00 Mama anrufen»*, *«in 2 Stunden Pause»*, *«20.11. 9 Uhr Zahnarzt»*. Oder **📅 Mit Datum wählen**. Connor meldet sich mit Benachrichtigung und Popup («In 10 Minuten nochmal» oder «Erledigt»).

> Erinnerungen funktionieren, solange Connor läuft. Beim Schliessen des Fensters läuft er im Tray (unten rechts) weiter. Tipp: In den Einstellungen **«Connor mit Windows starten»** aktivieren. Verpasste Erinnerungen zeigt er beim nächsten Start.

### Einstellungen (⚙ oben rechts)
Modell, Kreativität (je Modus), Standard-Modus, Kontextgrösse, Denkmodus, wie lange das Modell geladen bleibt, dein Name, Extra-Wünsche an Connors Persönlichkeit, Websuche (Suchmaschine, Seiten lesen), Spracherkennung (Modell, Sprache, Grafikkarte/Prozessor), Tray und Autostart.

### Spracherkennung
Beim ersten Diktieren lädt Connor das Sprachmodell herunter (Standard «Small», ca. 480 MB – einmalig). Danach läuft alles offline. Hast du eine NVIDIA-Grafikkarte mit installiertem CUDA, rechnet Connor dort, sonst auf dem Prozessor – das geht auch flott. Für noch genauere Ergebnisse in den Einstellungen «Large v3 Turbo» wählen.

---

## Update und Backup

- **Update:** Doppelklick auf `update.bat` – holt die neuste Version von GitHub und installiert neue Pakete. Danach Connor mit `start.bat` neu starten. Deine Daten bleiben unverändert und werden automatisch übernommen.
  *(Ohne `update.bat`: `git pull` und dann `start.bat` – das installiert neue Pakete ebenfalls automatisch.)*
- **Backup:** Den Ordner `data/` kopieren. Zum Wiederherstellen zurückkopieren.
- **Auf einen anderen PC umziehen:** Connor dort installieren und den `data/`-Ordner hineinkopieren.

---

## Probleme?

| Problem | Lösung |
|---|---|
| «Ollama läuft nicht» | Ollama starten (Startmenü) oder installieren. Connor versucht es beim Start auch selbst. Danach oben rechts auf ↻ klicken. |
| «Kein KI-Modell installiert» | Im Terminal `ollama pull qwen3:8b` und dann ↻ klicken. |
| Die erste Antwort dauert lange | Beim Start lädt Connor das Modell im Hintergrund – nach ein paar Sekunden geht's schneller. Unter Einstellungen → «Modell bereithalten» kannst du einstellen, wie lange es geladen bleibt. |
| Antworten sind generell sehr langsam | Im Terminal `ollama ps` – steht dort «CPU», ist die Grafikkarte zu klein. Kleineres Modell wählen (`qwen3:4b`), Denkmodus auf «Aus» lassen, Kontextgrösse verkleinern. |
| Connor sagt «ich suche» und hört auf | Passiert mit Version 1.1 nicht mehr: Connor sucht dann selbst. Sicher geht's mit dem **🌐**-Knopf oder `/suche …`. |
| Connor speichert nichts von selbst | Das Modell kann keine Tools → anderes Modell wählen oder `/merke` benutzen. |
| Websuche klappt nicht | Internet prüfen. Blockiert eine Suchmaschine, weicht Connor automatisch aus (DuckDuckGo → Bing → Wikipedia). Virenscanner, die HTTPS prüfen, sind dank Windows-Zertifikaten kein Problem. |
| 🎤 «Spracherkennung nicht installiert» | `start.bat` oder `update.bat` nochmal ausführen. Klappt die Installation nicht, ist deine Python-Version vielleicht zu neu – Python 3.12 oder 3.13 funktioniert sicher. |
| 🎤 Es wird nur Stille aufgenommen | Windows → Einstellungen → Datenschutz und Sicherheit → Mikrofon → «Desktop-Apps den Zugriff erlauben». |
| 🎤 Sprachmodell lädt nicht | Beim ersten Mal braucht Connor Internet. Den Ordner `models/` kannst du löschen, dann lädt er neu. |
| Fenster verschwunden | Connor läuft im Tray (unten rechts, evtl. unter «^»). Klick aufs Icon. |
| Etwas anderes | In `data/connor.log` steht, was passiert ist. Zum Debuggen im Terminal starten: `.venv\Scripts\python.exe main.py` |

---

## Für Bastler

```
connor/
  engine.py       Gesprächslogik: Prompt bauen, Ollama fragen, Werkzeuge ausführen, Suche erzwingen
  prompts.py      Connors Persönlichkeit, die zwei Modi und Schreib-Vorlagen
  tools.py        Werkzeuge: Gedächtnis, Dokumente, Erinnerungen, Websuche
  database.py     SQLite: Chats, Gedächtnis, Dokumente (mit Volltextsuche), Erinnerungen, Geschichten
  documents.py    liest Markdown, Text, Word, ODT, PDF, EPUB, HTML und Audio
  files.py        erstellt Word-Dateien (auch im Drehbuch-Layout), Markdown und Text
  voice.py        Mikrofon und lokale Spracherkennung (faster-whisper)
  llm.py          Ollama-Anbindung (Streaming, Tools, Abbrechen, Modell vorladen)
  websearch.py    DuckDuckGo / Bing / Wikipedia / SearXNG + die besten Seiten lesen
  timeparse.py    versteht «morgen um 18 Uhr», «in 2 Stunden», «Freitag abends»
  gui/            das Fenster (PySide6 / Qt)
tests/            automatische Tests (mit einem simulierten Ollama)
```

Connors Persönlichkeit steht in `connor/prompts.py` (`BASE`, `WRITER_MODE`, `ASSISTANT_MODE`) – dort kannst du sie anpassen. Für kleine Wünsche reicht das Feld «Extra-Wünsche» in den Einstellungen.

Tests ausführen:
```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```
