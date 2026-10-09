# Raumstempel (AutoCAD) → Revit-Räume

Dynamo-Skript für **Revit 2025** (Dynamo mit CPython 3, keine Zusatzsoftware).
Datei: `raumstempel/raumstempel_dynamo.py` (Inhalt in einen Python-Script-Node kopieren).

> **Wichtig:** Der Revit-Teil konnte nicht in Revit ausgeführt werden. Getestet ist die
> Logik (CSV, Kürzung, Ebenen, Koordinaten, Zuordnung, Listen) mit `python3 -m unittest discover -s tests -v`.
> Der erste Lauf gehört an eine **Modellkopie** (siehe Testplan).

## 1. Was das Skript tut

* Liest alle `*.csv` und `*.xlsx` eines Ordners (je Geschoss eine Datei **oder eine Datei für alle Geschosse**).
* **Raumname:** wird unverändert aus der CSV übernommen (auch `n.v.`), nur Leerzeichen am Rand entfallen.
* **Ignoriert Zeilen aus externen Referenzen:** Ist der `Dateiname` einer Zeile `…_Bestand.dwg`
  (z. B. `100049_004_A_G03_Bestand.dwg`), wird sie nicht verwendet. Das Suffix ist in Eingabe 15 änderbar.
* Ordnet jeden Stempel über den **Geschosscode in der OKS** einer Revit-Ebene zu
  (`G00` = EG, `G01` = 1. OG, `G02` = 2. OG, `G03` = 3. OG, `G04` = 4. OG, `U01` = 1. UG).
  Ebenen **ohne Räume** (nicht modelliert) oder ohne Zuordnung werden übersprungen und gemeldet.
* Nutzt nur Räume der Phase **Bestand** (Eingabe änderbar). Räume anderer Phasen werden ignoriert.
* Rechnet die Stempelkoordinaten (DWG-Einheit m) mit der Transformation einer **DWG-Verknüpfung**
  in Revit-Koordinaten um. Gesucht wird zum CSV-Dateinamen `…_G03.dwg` zuerst die Verknüpfung
  `…_G03_Bestand.dwg`, sonst `…_G03.dwg` (siehe „Zeichnungsstruktur“).
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

### Zeichnungsstruktur (zwei DWG je Geschoss)

| Datei                | Inhalt                                                   | In der CSV              | In Revit        |
|----------------------|----------------------------------------------------------|-------------------------|-----------------|
| `…_G03.dwg`          | nur AutoCAD-Architecture-Räume, Geschosse, Raumstempel; hat die `_Bestand` als Xref | Stempel werden **verwendet** | nicht verknüpft |
| `…_G03_Bestand.dwg`  | Wände, Türen, Zeichnung; teils Stempel gleichen Blocknamens | Zeilen werden **ignoriert** | **verknüpft**, liefert die Transformation |

Beide Zeichnungen haben denselben Nullpunkt und dieselben Einheiten. Deshalb werden die Koordinaten
der Stempel aus `…_G03.dwg` mit der Transformation (Versatz/Drehung) der Revit-Verknüpfung
`…_G03_Bestand.dwg` umgerechnet. Heißt die Verknüpfung in Revit anders, hilft Eingabe 13
(`["…_G03.dwg=Verknüpfungsname.dwg"]`). Gibt es zu einem Geschoss in der CSV nur Zeilen aus `_Bestand`
(Stempel dort mit anderem Block/Attributen), meldet das Skript „Geschoss … nur Zeilen aus externen
Referenzen“ und verarbeitet es nicht.

### Wichtig: DWG müssen in Revit verknüpft sein

Das Skript liest **keine DWG-Dateien von der Festplatte**. Es nimmt nur CAD-Verknüpfungen, die im
Revit-Modell vorhanden sind (*Einfügen → CAD verknüpfen*; Kontrolle unter *Verwalten → Verknüpfungen
verwalten → CAD-Formate*). DWG-Dateien im Projektordner genügen nicht. Fehlt die Verknüpfung, schreibt
das Protokoll „CAD-Instanzen im Modell: 0 …“ bzw. die Namen, die es gefunden hat.

### Ausgabelisten als Excel-Datei (Standard)

* Das Skript schreibt `Zuordnungsliste_<Zeit>.xlsx` und `Pruefliste_<Zeit>.xlsx` in den Ausgabeordner.
  Texte (z. B. Raumnummer `1.06`) stehen als **Text**, Zahlen als Zahlen. Es gibt **keine Datumsprobleme**
  mehr. Kopfzeile fixiert, Filter gesetzt, Spalten angepasst, „unsichere“ Zeilen hellgelb, in der Spalte
  **Freigabe** eine Auswahlliste `J`/`N`.
* Eingabe 16 (Ausgabeformat): `"xlsx"` (Standard), `"csv"` oder `"beides"`. Die CSV-Variante schreibt
  Nummern als `="1.06"` und Flächen mit Dezimalkomma (öffnet ebenfalls ohne Datumsproblem).
* Bearbeiten musst du nur die Spalten **Freigabe** (`J`/`N`) und bei Bedarf **Raum_ID**. Normal
  speichern (`.xlsx`).
* **Lauf 2** (Eingabe 11) nimmt die Liste als `.xlsx` **oder** `.csv`. Aus der Liste kommen nur
  OKS → Raum_ID und die Freigabe. **Name und Nummer der Stempel** liest das Skript aus den
  Original-Stempeldateien (Ordner in Eingabe 0). Eine verfälschte Spalte richtet also keinen Schaden an.
* Die Datei darf beim Lauf nicht in Excel geöffnet sein (Schreibfehler → das Skript weicht dann auf
  Dokumente/Temp aus und meldet das).
* Ältere CSV-Listen mit Datumsproblemen: *Daten → Aus Text/CSV*, UTF-8, Semikolon, Datentyperkennung
  **„Nicht erkennen“**.

### Stempel tauschen (Spalten `OKS`, `OKS_Tausch` und `Raum_ID`)

In der Zuordnungsliste entscheiden **nur diese Spalten**: `OKS` (= Stempel-ID, der Vorschlag des Skripts),
`OKS_Tausch` (deine Korrektur), `Raum_ID` (*in welchen Raum*) und `Freigabe`.

* **Regel:** Ist `OKS_Tausch` gefüllt, gilt dieser Wert. Ist sie leer, gilt die Spalte `OKS`.
  Der Vorschlag in `OKS` bleibt unverändert stehen, du siehst also jede Korrektur.
* Lauf 2 holt **Name und Raumnummer des Stempels mit der gültigen OKS** aus den Stempeldateien und
  schreibt sie in den Raum der Zeile. Die Spalten `Stempel_Nummer`, `Stempel_Name`, `Stempel_Flaeche`,
  `Methode`, `Status` und `Abweichung_Prozent` werden **nicht** gelesen und zeigen nach einem Tausch noch den
  Vorschlagsstempel; sie sind nur zur Orientierung bei der Prüfung da.
* Ändert man nur die `Raum_ID`, bekommt ein anderer Raum die Werte desselben Stempels.
* Die OKS (auch in `OKS_Tausch`) muss **exakt** wie in den Stempeldateien stehen (z. B.
  `100049-004-A-G01-_17`; Leerzeichen am Rand sind egal). Findet das Skript sie dort nicht, schreibt es die
  Zeile **nicht** („OKS nicht in Stempeldaten“, Prüfliste).
* Eine Zeile mit `OKS_Tausch`, aber ohne `J` in `Freigabe`, wird nicht geschrieben; das Protokoll weist
  darauf hin.
* Ältere Listen ohne die Spalte `OKS_Tausch` funktionieren weiter.
* Gehört die OKS zu einem anderen Geschoss als die Ebene des Raums (Geschosscode `G01` bei einem Raum
  auf dem EG), schreibt das Skript die Zeile ebenfalls nicht („Geschoss passt nicht“).
* Steht dieselbe OKS in zwei Zeilen mit verschiedenen Räumen, schreibt es beide nicht (Doppelte).

### Die drei Blätter der Excel-Zuordnungsliste

| Blatt         | Inhalt | Zweck |
|---------------|--------|-------|
| **Zuordnung** | je Stempel eine Zeile (Vorschlag des Skripts) | hier arbeitest du; **Lauf 2 liest nur dieses Blatt** |
| **Stempel**   | alle gelesenen Stempel: Ebene, Geschoss, OKS, Name, Nummer, Fläche, Status, Raum-Vorschlag, Quelle | Stempel finden; Status `zugeordnet` / `frei` / `Ebene nicht verarbeitet` (nicht zugeordnete Zeilen hellgelb) |
| **Räume**     | alle Räume der Phase Bestand: Ebene, Raum_ID, Nummer, Name, Fläche, aktuelle `RaumOKS`, Stempel-Vorschlag, Status | Raum_ID finden; Status `zugeordnet` / `ohne Stempel` / `nicht platziert` (ohne Stempel hellgelb) |

* In der Spalte **OKS_Tausch** (Blatt Zuordnung) gibt es eine **Auswahlliste** mit allen Stempel-IDs, in der
  Spalte **Raum_ID** eine Auswahlliste mit allen Räumen. Die Listen entstehen aus den Blättern Stempel und
  Räume. Mit den Filtern in den Blättern (nach Ebene, Status) findest du schnell einen freien Stempel.
* Die Blätter **Stempel** und **Räume** werden beim nächsten Lauf 1 neu geschrieben; Lauf 2 liest sie nicht.
  Benenne das Blatt **Zuordnung** nicht um (sonst liest Lauf 2 ersatzweise das erste Blatt).
* Die Zusatzblätter gibt es nur in der Excel-Ausgabe (Eingabe 16 = `"xlsx"` oder `"beides"`), nicht in der CSV.

### Lauf 2 mehrfach ausführen (Nacharbeit der unklaren Stempel)

Empfohlener Ablauf: Lauf 1 → Liste prüfen → Lauf 2 (die sicheren Zeilen sind `J`) → in **derselben Liste**
die unklaren Zeilen klären (`J` setzen, ggf. `Raum_ID` ändern) → Lauf 2 erneut mit derselben Liste.

* Das ist unkritisch: bereits geschriebene Zeilen werden als „unverändert“ übersprungen. Nur neue
  Freigaben werden geschrieben. Der Lauf ist eine Revit-Transaktion (Strg + Z macht ihn rückgängig).
* **Nicht** zwischendurch Lauf 1 neu starten und mit der neuen Liste weiterarbeiten: sie setzt `Freigabe`
  wieder auf die Vorschläge zurück, deine Entscheidungen aus der alten Liste fehlen darin.
* Das Skript prüft vor dem Schreiben und **schreibt nicht**, wenn dadurch Doppelte entstünden. Die
  Prüfliste nennt dann die Raum-IDs:
  * *Raum mehrfach in der Liste*: zwei freigegebene Zeilen zeigen auf denselben Raum.
  * *OKS bereits an anderem Raum*: du hast einen schon geschriebenen Stempel einem anderen Raum zugewiesen.
    Der alte Raum behält seine Werte. Lösche dort `RaumOKS` (z. B. in einer Raumliste) oder gib ihm einen
    anderen Stempel, dann Lauf 2 wiederholen. Vertauschte Zuordnungen (zwei Räume tauschen ihre Stempel)
    sind erlaubt.
  * *Doppelte Nummer*: die Raumnummer ist an einem anderen Raum vergeben; die übrigen Werte werden
    geschrieben, nur die Nummer nicht.
* Werte, die du nach Lauf 2 in Revit von Hand geändert hast (Name, Nummer), werden bei Zeilen mit `J`
  wieder überschrieben. Setze solche Zeilen auf `N`.

### Mehrere Dateien im Ordner

Alle `*.csv` **und `*.xlsx`** des Ordners werden als Stempeldateien gelesen (die AutoCAD-Datenextraktion
kann beides ausgeben). `Zuordnungsliste_*`, `Pruefliste_*` und Excel-Sperrdateien (`~$…`) werden
übersprungen. Liegen dieselben Stempel in zwei Dateien (z. B. eine verkleinerte Testdatei und die volle
Datei), wird jeder identische Stempel **einmal** verwendet (Hinweis im Protokoll). Nur **widersprüchliche**
Doppelte (gleiche OKS, andere Werte) werden nicht verarbeitet. Besser: nicht benötigte Dateien aus dem
Ordner nehmen.

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
`Dateiname` (auch `RaumOKS`, `Raumnummer`, `Raumname`, `Raumfläche`).

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
     false,                                           // 2  (frei, nicht mehr verwendet; Wert egal)
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
     "_Bestand",                                      // 15 Zeilen mit diesem Dateinamen-Ende ignorieren ("-" = keine)
     "xlsx"                                           // 16 Ausgabeformat der Listen: "xlsx", "csv" oder "beides"
   ];
   ```
3. **Watch-Node** (`Watch`) aus der Bibliothek holen.
4. Verbinden: Ausgang des Code-Blocks → `IN[0]` des Python-Nodes → Eingang des Watch-Nodes.
5. Unten links die Ausführung von *Automatisch* auf **Manuell** stellen, dann **Ausführen**.
6. **Datei → Speichern unter…** → `Raumstempel.dyn`.

Für **Lauf 2** im Code-Block `true` an Position 1 auf `false` ändern und bei Position 11 den Pfad der
geprüften Zuordnungsliste eintragen, dann erneut ausführen. Danach den Graphen speichern.

Alternativ lassen sich alle 17 Werte auch einzeln über 17 Eingänge verbinden (IN[0] … IN[16]). Dann
müssen alle Eingänge belegt sein; für „leer“ einen Code-Block mit `null;` oder `"";` verwenden.

## 4. Ablauf in Revit

1. Räume der Phase **Bestand** vorher mit „Alle Räume automatisch platzieren“ erzeugen und kontrollieren
   (das Skript legt standardmäßig keine Räume an).
2. DWG-Verknüpfungen müssen im Projekt vorhanden sein (Name = Dateiname der CSV-Spalte `Dateiname`).
3. **Lauf 1:** Trockenlauf = True. Protokoll, `Zuordnungsliste_*.xlsx` und `Pruefliste_*.xlsx` im
   Ausgabeordner ansehen. Trefferquote beachten (niedrig ⇒ Versatz/Einheit/Verknüpfung).
4. Zuordnungsliste in Excel prüfen. Spalte **Freigabe** auf `J` (schreiben) oder `N` setzen,
   bei Bedarf `Raum_ID` korrigieren. Speichern, Excel schließen.
5. **Lauf 2:** Pfad der Liste in IN[11], Trockenlauf = False. Geschrieben wird in **einer Transaktion**,
   nur bei geänderten Werten (mehrfach ausführbar). Fehlen `RaumOKS`/`Raumnummer_Text`, werden sie
   gebunden – bevorzugt aus der Firmen-Datei (IN[10] bzw. aktuell in Revit eingestellte Datei);
   nur ersatzweise in einer eigenen Datei (deutlicher Hinweis, neue GUID!).
6. **Geschrieben wird nur mit Zuordnungsliste.** Das Protokoll nennt in der Zeile `Modus:` den Lauf:
   `LAUF 1` (keine Liste, nichts wird geschrieben) oder `LAUF 2` (Liste aus Position 11). Ist der
   Trockenlauf aus, aber Position 11 leer, bricht das Skript mit einer Fehlermeldung ab und schreibt
   nichts (sonst würde es nur eine neue Vorschlagsliste erzeugen und deine bearbeitete Liste ignorieren).
   Dasselbe passiert, wenn der Listenpfad versehentlich in Position 10 oder 12 steht. Räume werden vom
   Skript nie angelegt: lege sie vorher in Revit an (Phase „Bestand“).

### Prüfliste (CSV, mit Element-IDs)

Räume ohne Stempel · Stempel ohne Raum · Räume mit mehreren Stempeln · unsichere Zuordnungen ·
doppelte OKS · doppelte/ungültige gekürzte Nummern · nicht beschreibbare Räume (Workset belegt) ·
nicht verarbeitete Ebenen · Flächenabweichung ≥ 5 % · fehlende Parameter · Koordinaten-Warnung.

## 5. Testplan für den ersten Lauf (Modellkopie!)

1. Kopie des Projekts speichern („Zentralmodell lösen“ ist nicht nötig; bei Worksets alle Räume sich aneignen).
2. Testdatei `tests/daten/100049_004_A_G03_Test.csv` (23 Stempel, G03) in einen leeren Ordner legen.
3. Vorbedingung: Ebene „3. OG“ mit Räumen der Phase Bestand, DWG `…_G03_Bestand.dwg` verknüpft
   (die Stempel-CSV mit `…_G03.dwg`-Zeilen; `…_Bestand`-Zeilen werden ignoriert).
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
* MEP-Raumnummer und Solar-Computer sind noch nicht umgesetzt.
