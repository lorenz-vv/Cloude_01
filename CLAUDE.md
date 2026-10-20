# Projektstand: Raumstempel (AutoCAD) -> Revit-Räume

Wird von Claude Code beim Start jeder Sitzung in diesem Repository automatisch gelesen.
Alle Antworten, Kommentare und Meldungen auf Deutsch.

## Was fertig ist (Stand: 20.10.2026)
* `raumstempel/raumstempel_dynamo.py`: Dynamo-Python-Node (CPython3, Revit 2025, nur Standardbibliothek).
  Überträgt Raumstempel aus der AutoCAD-Datenextraktion (CSV/XLSX) in vorhandene Revit-Räume
  (Phase "Bestand"): Name -> ROOM_NAME, RaumOKS und Raumnummer_Text (Shared Parameter, Präfix "Raum-Nr. "),
  ROOM_NUMBER = Geschosscode + letztes OKS-Segment (`...-G01-_15` -> `G01-_15`).
* Zwei Läufe: Lauf 1 (Trockenlauf = true) schreibt nur die Zuordnungsliste (Excel) und Prüfliste.
  Lauf 2 (Trockenlauf = false) schreibt die Zeilen mit Freigabe J aus der NEUESTEN Liste im
  Ausgabeordner (Fehlermeldung, wenn sie in Excel noch offen ist).
* Zuordnung: Raumtag (Revit-Raumtag neben dem alten Stempeltext, Radius 1,5 m) > Punkt-in-Raum > Fläche.
  Stempeltausch über Spalte `OKS_Tausch`.
* Code-Block mit 19 Eingaben (Positionen 0-18, Position 2 unbenutzt): siehe `ANLEITUNG.md`.
* Tests: `python3 -m unittest discover -s tests` (143 Tests, mit nachgebildetem Revit in `tests/fake_revit.py`).
* Im echten Projekt von Lorenz bestätigt: Lauf 1 und Lauf 2 funktionieren, Raumtags sind "ein Gamechanger".
  Das Skript gilt als endgültig; Änderungen nur auf ausdrücklichen Wunsch.

## Wichtige Entscheidungen
* Stempelfläche wird nur zum Vergleich genutzt, nie geschrieben. Räume werden nie angelegt.
* Zeilen mit Dateiname `..._Bestand.dwg` (externe Referenz) werden ignoriert.
* DWG müssen in Revit verknüpft sein (`..._Bestand.dwg`, gleicher Nullpunkt wie die Stempel-DWG).
* Ebenen aus OKS-Code: G00=EG, G01=1. OG, G02, G03, G04, U01=1. UG.
* Zurückgestellt: MEP-Raumnummer / Solar-Computer; Firmen-Shared-Parameter-Datei (Eingabe 10).

## Nächstes Thema (neuer Chat)
Lorenz erstellt in Revit eine Zeichnung für die TGA (Technische Gebäudeausrüstung). Das Revit-Modell mit den
eingetragenen Raumstempeln ist als Revit-Verknüpfung in das TGA-Modell eingebunden. Offene Fragen:
Welche Raumdaten (Name, Nummer, RaumOKS, Raumnummer_Text) sollen in der TGA-Zeichnung erscheinen
(Raumtags/Räume aus dem verknüpften Modell, Bereiche/MEP-Räume im TGA-Modell, Pläne/Schriftfelder)?
Hinweis: Shared Parameter aus verknüpften Modellen lassen sich in Tags nutzen, wenn der Parameter im
TGA-Projekt als Raumtag-Beschriftung für "Räume in Verknüpfungen" verfügbar ist.
