# Raumstempel (AutoCAD) → Revit-Räume

Dynamo-Skript für **Revit 2025** (Dynamo mit CPython 3, keine Zusatzsoftware).
Datei: `raumstempel/raumstempel_dynamo.py` (Inhalt in einen Python-Script-Node kopieren).

> **Wichtig:** Der Revit-Teil konnte nicht in Revit ausgeführt werden. Getestet ist die
> Logik (CSV, Kürzung, Ebenen, Koordinaten, Zuordnung, Listen) mit `python3 -m unittest discover -s tests -v`.
> Der erste Lauf gehört an eine **Modellkopie** (siehe Testplan).

## 1. Was das Skript tut

* Liest alle `*.csv` eines Ordners (je Geschoss eine Datei **oder eine Datei für alle Geschosse**).
* **Ignoriert Zeilen aus externen Referenzen:** Ist der `Dateiname` einer Zeile `…_Bestand.dwg`
  (z. B. `100049_004_A_G03_Bestand.dwg`), wird sie nicht verwendet. Das Suffix ist in Eingabe 15 änderbar.
* Ordnet jeden Stempel über den **Geschosscode in der OKS** einer Revit-Ebene zu
  (`G00` = EG, `G01` = 1. OG, `G02` = 2. OG, `G03` = 3. OG, `G04` = 4. OG, `U01` = 1. UG).
  Ebenen **ohne Räume** (nicht modelliert) oder ohne Zuordnung werden übersprungen und gemeldet.
* Nutzt nur Räume der Phase **Bestand** (Eingabe änderbar). Räume anderer Phasen werden ignoriert.
* Rechnet die Stempelkoordinaten (DWG-Einheit m) mit der Transformation der **DWG-Verknüpfung**
  in Revit-Koordinaten um. Die Verknüpfung wird über die CSV-Spalte `Dateiname` gefunden.
* Prüfpunkt-Höhe: Ebenenhöhe + 1 m. Test mit `Room.IsPointInRoom`.
* **Zuordnung in drei Stufen** (wichtig, weil Stempel teils außerhalb ihres Raums liegen):
  1. *Position*: Liegen mehrere Stempel im selben Raum (z. B. 3 im Treppenhaus), gewinnt der
     mit der besten Flächenübereinstimmung (`FM.FLAECHE` ↔ Revit-Fläche). Die übrigen werden frei.
  2. *Fläche*: Freie Stempel und Räume ohne Stempel werden über die Fläche gepaart, bei ähnlichen
     Flächen entscheidet der Abstand (Status dann „unsicher“).
  3. *Position, Fläche weicht ab*: leerer Raum mit freiem Stempel darin → Vorschlag „unsicher“.
* Status **sicher** = Flächenabweichung ≤ 5 % und eindeutig. Alles andere ist **unsicher**
  und wird nur nach deiner Freigabe geschrieben.
* Die Stempelfläche wird **nie** geschrieben, nur verglichen (Abweichung ab 5 % in der Prüfliste).

### Geschriebene Parameter

| Quelle (CSV)   | Revit-Raumparameter                         | Beispiel                |
|----------------|---------------------------------------------|-------------------------|
| `FM.NAME`      | Name (`ROOM_NAME`)                          | Büro                    |
| `FM.OKS`       | Shared Parameter `RaumOKS`                  | 100049-004-A-G03-_01    |
| `FM.NUMMER`    | Shared Parameter `Raumnummer_Text` + Präfix | Raum-Nr. 4.01 / Raum-Nr. n.v. |
| `FM.OKS` gekürzt | Nummer (`ROOM_NUMBER`)                    | G03-_01                 |

Kürzung: **Geschosscode + „-“ + letztes OKS-Segment** (`…-G01-_15` → `G01-_15`, funktioniert auch bei
3-stelligem Zähler). Duplikate (auch gegenüber bereits vergebenen Raumnummern im Projekt) werden gemeldet;
die Nummer wird dann nicht geschrieben.

## 2. AutoCAD-Export

Befehl `DATENEXTRAKTION`, nur Block `PIT_DOI_GMSH`. Im Assistenten:

1. Eigenschaften: Kategorie **Attribut** → `FM.OKS`, `FM.NUMMER`, `FM.NAME` (optional `FM.FLAECHE`);
   Kategorie **Geometrie** → `Position X`, `Position Y`. Spalte `Dateiname` mit ausgeben.
2. „Anzahl-Spalte“ und „Namenspalte“ ausschalten, **keine** Zusammenfassung gleicher Zeilen.
3. Ausgabe als **CSV**. Position X/Y mit mindestens 3 Dezimalstellen.
4. Datei je Geschoss in **einen Ordner** legen. Trennzeichen (Komma/Semikolon), Dezimalpunkt/-komma,
   UTF-8 oder Windows-1252 erkennt das Skript selbst. Weitere Spalten schaden nicht.

Spaltennamen, die erkannt werden: `FM.OKS`, `FM.NUMMER`, `FM.NAME`, `FM.FLAECHE`, `Position X/Y`,
`Dateiname` (auch `RaumOKS`, `Raumnummer`, `Raumname`, `Raumfläche`). Optional `Ziel X` / `Ziel Y`:
Ankerpunkt der Verbindungslinie, falls er später mitexportiert wird – wird dann statt des Einfügepunkts
für den Punkt-in-Raum-Test verwendet.

## 3. Dynamo-Graph (.dyn) selbst erstellen – 3 Nodes

Revit 2025 → Registerkarte *Verwalten* → **Dynamo** → *Neu*.

1. **Python-Node:** Suchfeld oben in der Bibliothek: `Python Script` → in den Arbeitsbereich ziehen.
   Unten am Node die Engine auf **CPython3** stellen. Beim Node sind standardmäßig mehrere Eingänge
   vorhanden; es wird nur `IN[0]` benutzt (überzählige Eingänge mit „−“ entfernen oder leer lassen).
   Doppelklick auf den Node → den **kompletten Inhalt** von `raumstempel/raumstempel_dynamo.py`
   einfügen (vorhandenen Text vorher löschen) → **Änderungen speichern**.
2. **Code Block:** Doppelklick in den leeren Arbeitsbereich erzeugt einen Code-Block. Diesen Text einfügen
   und den Ordnerpfad anpassen (Schrägstriche `/` statt `\`):

   ```
   [
     "U:/Dokumente/BIM_CAD/Raumstempel",            // 0  Ordner mit den CSV-Dateien
     true,                                            // 1  Trockenlauf (true = nichts schreiben)
     false,                                           // 2  fehlende Räume anlegen
     ["G00=EG","G01=1. OG","G02=2. OG","G03=3. OG","G04=4. OG","U01=1. UG"],  // 3 Ebenen
     "m",                                             // 4  Einheit der DWG
     "Bestand",                                       // 5  Phase
     5,                                               // 6  Schwelle "sicher" in %
     15,                                              // 7  Obergrenze Vorschlag in %
     10,                                              // 8  max. Abstand (Fläche) in m
     "Raum-Nr. ",                                     // 9  Präfix Raumnummer_Text
     "",                                              // 10 Firmen-Shared-Parameter-Datei (optional)
     "",                                              // 11 geprüfte Zuordnungsliste (Lauf 2)
     "",                                              // 12 Ausgabeordner (leer = <Ordner>/_Ausgabe)
     [],                                              // 13 manuelle Verknüpfungszuordnung
     true,                                            // 14 Parameter bei Bedarf anlegen
     "_Bestand"                                       // 15 Zeilen mit diesem Dateinamen-Ende ignorieren ("-" = keine)
   ];
   ```
3. **Watch-Node** (`Watch`) aus der Bibliothek holen.
4. Verbinden: Ausgang des Code-Blocks → `IN[0]` des Python-Nodes → Eingang des Watch-Nodes.
5. Unten links die Ausführung von *Automatisch* auf **Manuell** stellen, dann **Ausführen**.
6. **Datei → Speichern unter…** → `Raumstempel.dyn`.

Für **Lauf 2** im Code-Block `true` an Position 1 auf `false` ändern und bei Position 11 den Pfad der
geprüften Zuordnungsliste eintragen, dann erneut ausführen. Danach den Graphen speichern.

Alternativ lassen sich alle 16 Werte auch einzeln über 15 Eingänge verbinden (IN[0] … IN[14]). Dann
müssen alle Eingänge belegt sein; für „leer“ einen Code-Block mit `null;` oder `"";` verwenden.

## 4. Ablauf in Revit

1. Räume der Phase **Bestand** vorher mit „Alle Räume automatisch platzieren“ erzeugen und kontrollieren
   (das Skript legt standardmäßig keine Räume an).
2. DWG-Verknüpfungen müssen im Projekt vorhanden sein (Name = Dateiname der CSV-Spalte `Dateiname`).
3. **Lauf 1:** Trockenlauf = True. Protokoll, `Zuordnungsliste_*.csv` und `Pruefliste_*.csv` im
   Ausgabeordner ansehen. Trefferquote beachten (niedrig ⇒ Versatz/Einheit/Verknüpfung).
4. Zuordnungsliste in Excel prüfen. Spalte **Freigabe** auf `J` (schreiben) oder `N` setzen,
   bei Bedarf `Raum_ID` korrigieren. Als CSV (Semikolon) speichern.
5. **Lauf 2:** Pfad der Liste in IN[11], Trockenlauf = False. Geschrieben wird in **einer Transaktion**,
   nur bei geänderten Werten (mehrfach ausführbar). Fehlen `RaumOKS`/`Raumnummer_Text`, werden sie
   gebunden – bevorzugt aus der Firmen-Datei (IN[10] bzw. aktuell in Revit eingestellte Datei);
   nur ersatzweise in einer eigenen Datei (deutlicher Hinweis, neue GUID!).
6. Ohne Liste und mit Trockenlauf = False schreibt das Skript nur Zuordnungen mit Status „sicher“.

### Prüfliste (CSV, mit Element-IDs)

Räume ohne Stempel · Stempel ohne Raum · Räume mit mehreren Stempeln · unsichere Zuordnungen ·
doppelte OKS · doppelte/ungültige gekürzte Nummern · nicht beschreibbare Räume (Workset belegt) ·
nicht verarbeitete Ebenen · Flächenabweichung ≥ 5 % · fehlende Parameter · Koordinaten-Warnung.

## 5. Testplan für den ersten Lauf (Modellkopie!)

1. Kopie des Projekts speichern („Zentralmodell lösen“ ist nicht nötig; bei Worksets alle Räume sich aneignen).
2. Testdatei `tests/daten/100049_004_A_G03_Test.csv` (23 Stempel, G03) in einen leeren Ordner legen.
3. Vorbedingung: Ebene „3. OG“ mit Räumen der Phase Bestand, DWG `100049_004_A_G03_Test.dwg` verknüpft.
4. **Trockenlauf.** Erwartung: Ebenenbericht „G03 → 3. OG ok“; Plausibilitätsmeldung „x von 23 Punkten im
   Umriss der Verknüpfung“ ohne Warnung; Trefferquote notieren. Kommen die Punkte nicht an: Einheit (m),
   Verknüpfungsposition und Verknüpfungsname prüfen.
5. Zuordnungsliste durchsehen: Treppenhaus-Stempel (Treppenhaus 1 / Aufzug / „nicht bekannt“) landen in
   den richtigen Räumen? Stichprobe von 5 Räumen gegen den Plan.
6. Prüfliste: Räume ohne Stempel, Stempel ohne Raum – plausibel?
7. **Lauf 2** an der Kopie mit Freigabe `J`. Danach in Revit nachsehen: Name, Nummer (`G03-_01`),
   `RaumOKS`, `Raumnummer_Text` (`Raum-Nr. 4.01`). Fläche unverändert.
8. **Lauf 2 wiederholen.** Erwartung: „geschrieben: 0“, alles „unverändert“.
9. Negativtests: CSV mit unbekanntem Geschosscode (z. B. `G09`) → Ebene wird gemeldet und übersprungen;
   Ebene ohne Räume → übersprungen; Raum von anderem Benutzer belegt → gemeldet, kein Abbruch;
   zweiter Raum mit gleicher Nummer → Duplikat gemeldet.
10. Erst danach am Originalmodell arbeiten.

## 6. Bekannte Grenzen / Annahmen

* DWG-Koordinaten werden zuerst von m in Fuß umgerechnet und dann mit `GetTotalTransform()` der
  Verknüpfung transformiert. Falls Revit die Einheitenskalierung bereits in der Transformation führt,
  zeigt das die Plausibilitätsmeldung (Punkte außerhalb des Verknüpfungsumrisses) – bitte dann melden.
* Einfügepunkte außerhalb ihres Raums werden über die Fläche aufgefangen. Das ist robust bei
  unterschiedlich großen Räumen; bei vielen gleich großen Räumen entscheidet der Abstand (Status „unsicher“).
* „Fehlende Räume anlegen“ nutzt den Stempel-Einfügepunkt; bei Stempeln außerhalb ihres Raums ist das
  nicht sinnvoll. Standard bleibt aus.
* MEP-Raumnummer und Solar-Computer sind noch nicht umgesetzt.
