# Tev1 Demo - KI-Klassifizierer

Eine kleine Browser-Demo fuer **KI in der Beratungsbranche**. Texte eingeben, eine Frage stellen und die Zuordnung samt Modellbewertung und Antwortzeit sehen. Alle vorbereiteten Beispiele sind fiktiv.

## Auf dem Windows-Laptop starten

1. Hier auf GitHub **Code > Download ZIP** waehlen und die ZIP-Datei herunterladen.
2. Die ZIP-Datei mit **Alle extrahieren** vollstaendig entpacken.
3. Im entpackten Ordner **Start-Demo.cmd** doppelt anklicken.

Der Starter erledigt automatisch:

- Eine kleine, gepruefte Python-Laufzeit in den Projektordner herunterladen. Eine eigene Python-Installation ist nicht erforderlich.
- Den lokalen Demo-Server starten und die Demo sofort im Standardbrowser oeffnen.
- Im Browser erscheint beim ersten Start eine Seite **Einmalige Einrichtung** mit Ladebalken, Downloadgroesse, Geschwindigkeit und geschaetzter Restzeit. Dort laeuft nacheinander:
  1. Ollama pruefen und bei Bedarf herunterladen (offizieller Installer, Signaturpruefung) und ohne Administratorrechte installieren.
  2. Das Modell Tev1 herunterladen, falls es noch fehlt.
  3. Das Modell laden.
- Sobald alles bereit ist, wechselt die Seite automatisch zur Demo. Bei einem Abbruch (z. B. Internet weg) gibt es den Knopf **Erneut versuchen**; Downloads werden fortgesetzt.

**Beim ersten Start ist Internet erforderlich.** Ollama und das Modell zusammen benoetigen mehrere GB Download und Speicherplatz. Das kann einige Minuten dauern. Fuer Installation, Modell und Laufzeit mindestens 12 GB freien Speicher einplanen. Spaetere Starts verwenden die vorhandenen Dateien und funktionieren mit lokalem Modell auch ohne Internet. Es ist weder ein Ollama-Konto noch ein API-Schluessel erforderlich.

Windows kann bei heruntergeladenen Dateien eine Herkunftsmeldung anzeigen. Der Starter fordert keine Administratorrechte an. Fuer verwaltete Hochschul- oder Firmenrechner koennen eigene Installationsregeln gelten.

## Vorfuehren

Ein Beispiel fuer eine Einsatzmoeglichkeit waehlen, einen Beispieltext laden oder selbst schreiben, dann **Klassifizieren** klicken. Frage und Kategorien lassen sich aendern. Unter **Details** steht die echte Modellantwort.

Die angezeigte Zeit umfasst die gesamte Anfrage vom Browser ueber den lokalen Server zu Ollama und zurueck. Die Prozentwerte sind Modellbewertungen, keine gemessene Trefferquote. Das Modell wird beim Start vorbereitet und nach Anfragen fuer bis zu 30 Minuten ohne weitere Nutzung geladen gehalten. Nach laengerer Pause kann ein erneuter Modellstart noetig sein; dafuer gibt es **Modell vorbereiten** auf der Seite.

## Langsamer Laptop?

Die Geschwindigkeit haengt vom Laptop ab. Eine starke Grafikkarte hilft; fuer die Standardversion mit 4 Milliarden Parametern sind 16 GB Arbeitsspeicher ein sinnvoller Ausgangspunkt, aber keine Geschwindigkeitsgarantie.

Optional kann in PowerShell die kleinere, weniger genaue Version gestartet werden:

```powershell
.\Start-Demo.cmd -Model tev1:0.8b
```

Die kleine Version wird ebenfalls automatisch heruntergeladen. Der Modellname steht in der Ergebnisantwort unter **Details**. Bereits laufende Demo-Server fuer ein anderes Modell werden nicht umgestellt.

## Technik

- `index.html`: Single-Page-Oberflaeche ohne externe Bibliotheken.
- `server.py`: lokaler Python-Server; keine zusaetzlichen Python-Pakete.
- `Start-Demo.cmd` / `Start-Demo.ps1`: Einrichtung und Start unter Windows.
- `.runtime/`: automatisch erzeugte Laufzeit und lokale Protokolle (`setup.log` beschreibt die Einrichtung), nicht in Git versioniert.

Die Demo bindet nur an `127.0.0.1` und ist normalerweise unter `http://127.0.0.1:8787` erreichbar. Bei belegtem Port wird ein anderer lokaler Port gewaehlt. Eingabetexte und Antworten werden vom Demo-Server nicht gespeichert. Das Schliessen des Browserfensters beendet die Hintergrunddienste nicht. Ollama entlaedt das Modell nach der eingestellten Wartezeit.

Der Quellcode liegt auf GitHub; die KI laeuft auf dem eigenen Rechner. GitHub Pages kann das lokale Modell nicht fuer andere Besucher bereitstellen.

## Quellen und Lizenzen der Komponenten

- [Ollama fuer Windows](https://docs.ollama.com/windows) und [System-One-Schnittstelle](https://docs.ollama.com/api/systemone).
- [Tev1 bei Ollama](https://ollama.com/library/tev1) und [offizielle Modellkarte mit Lizenzhinweisen](https://huggingface.co/togethercomputer/Tev1-4B-experimental).
- [Python 3.13.16](https://www.python.org/downloads/release/python-31316/): portable Laufzeit von python.org, Pruefung gegen die dort veroeffentlichte SHA-256-Pruefsumme.

Die Modellgewichte werden separat ueber Ollama bezogen und sind nicht Teil dieses Repositorys. Tev1 ist experimentell. Die Modellkarte weist darauf hin, dass unter anderem Deutsch, Kalibrierung und Robustheit noch nicht umfassend untersucht sind.
