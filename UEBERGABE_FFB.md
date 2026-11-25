# Übergabe für das Projekt "Automatische Fertigfußböden (FFB) je Raum"

Herkunft: Projekt "Raumstempel -> Revit-Räume" (Repository `lorenz-vv/Cloude_01`, Branch
`claude/autocad-room-stamp-revit-mpcp1s`). Alles Wiederverwendbare steht hier; der vollständige Code liegt in
`raumstempel/raumstempel_dynamo.py` (rund 2.370 Zeilen, ein einziger Python-Node).

## 1. Umgebung
* Revit 2025 (Version laut Projekt). Die genaue Dynamo-Version haben wir nicht festgehalten; Revit 2025 bringt
  Dynamo 3.x mit. Bitte in Dynamo unter Hilfe > Über Dynamo nachsehen und im neuen Chat nennen.
* Python-Engine: **CPython3** (Python-Script-Node, pythonnet). Kein IronPython.
* Pakete: **keine**. Kein Clockwork, Data-Shapes oder Rhythm. Nur Python-Standardbibliothek plus Revit-API.
* Windows (Pfade wie `U:/Dokumente/...`; im Code Schrägstriche `/` statt `\`).
* Entwicklung/Tests laufen in einer Linux-Sandbox mit nachgebildetem Revit (`tests/fake_revit.py`).

## 2. Zweck und Ablauf des bestehenden Skripts
Überträgt Raumstempel aus der AutoCAD-Datenextraktion (CSV/XLSX) in vorhandene Revit-Räume (Phase "Bestand"):
Name -> `ROOM_NAME`, `RaumOKS` und `Raumnummer_Text` (Shared Parameter), `ROOM_NUMBER` aus Geschosscode + letztem
OKS-Segment. Zuordnung der Stempel zu Räumen: Raumtag > Punkt-in-Raum > Fläche. Zwei Läufe: Lauf 1 (Trockenlauf)
schreibt nur eine Excel-Zuordnungsliste; Lauf 2 schreibt die mit "J" freigegebenen Zeilen in einer Transaktion.
Die Logik ist von Revit getrennt (reine Funktionen, 143 Tests).

## 3. Code-Boilerplate (aus dem Skript, bewährt)
Der Code-Block in Dynamo übergibt alle Einstellungen als EINE Liste an `IN[0]`; das Skript entpackt sie.
Es gibt nur **einen** Python-Node. `UnwrapElement` wurde nicht gebraucht (keine Elemente als Eingabe).

```python
# -*- coding: utf-8 -*-
import csv, datetime, glob, io, math, os, re, zipfile      # nur Standardbibliothek

METER_JE_FUSS = 0.3048
M2_JE_FT2 = 0.09290304


class Revit(object):
    """Zugriff auf Revit/Dynamo. Wird erst zur Laufzeit geladen (nicht beim Import der Tests)."""

    def __init__(self):
        import clr
        clr.AddReference("RevitAPI")
        clr.AddReference("RevitServices")
        import Autodesk.Revit.DB as DB
        from RevitServices.Persistence import DocumentManager
        from RevitServices.Transactions import TransactionManager
        self.DB = DB
        self.doc = DocumentManager.Instance.CurrentDBDocument
        self.app = self.doc.Application
        self.TransactionManager = TransactionManager


def _eingaben_entpacken(eingaben):
    """Alle Eingaben als EINE Liste an IN[0] (ein Code-Block-Node)."""
    try:
        if len(eingaben) == 1 and isinstance(eingaben[0], (list, tuple)):
            return list(eingaben[0])
    except TypeError:
        pass
    return eingaben


def _eingabe(eingaben, i, standard):
    try:
        wert = eingaben[i]
    except (IndexError, KeyError, TypeError):
        return standard
    return standard if wert is None or wert == "" else wert


def _eid(element_id):
    """ElementId -> int (Revit 2024+: .Value, davor .IntegerValue)."""
    wert = getattr(element_id, "Value", None)
    if wert is None:
        wert = element_id.IntegerValue
    return int(wert)


def haupt(eingaben):
    """Einstieg aus dem Python-Node. Gibt das Protokoll als Liste von Textzeilen zurück."""
    zeilen = []
    try:
        ...                                   # eigentliche Arbeit
    except Exception as ex:                   # nichts darf unkommentiert abbrechen
        import traceback
        zeilen.append("FEHLER: %s" % ex)
        zeilen.append(traceback.format_exc())
    return zeilen


# Dynamo-Einstieg: IN/OUT stellt Dynamo bereit. Beim Import (Tests) passiert nichts.
if "IN" in globals():
    OUT = haupt(IN)  # noqa: F821
```

Namen von Revit-Elementen. In Dynamo/CPython wirft `Element.Name` an manchen Typen (z. B. `CADLinkType`)
"property cannot be read":

```python
def _elementname(el):
    if el is None:
        return ""
    try:
        return el.Name
    except Exception:
        pass
    try:
        import Autodesk.Revit.DB as DB_
        return DB_.Element.Name.__get__(el)          # über die Basisklasse
    except Exception:
        pass
    try:
        import Autodesk.Revit.DB as DB_
        for bip in (DB_.BuiltInParameter.SYMBOL_NAME_PARAM, DB_.BuiltInParameter.ALL_MODEL_TYPE_NAME):
            p = el.get_Parameter(bip)
            if p is not None and p.AsString():
                return p.AsString()
    except Exception:
        pass
    return ""
```

Räume lesen (Phase, Ebene, Fläche, Position), Punkt-in-Raum, Ebenen sammeln:

```python
sammler = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Rooms).WhereElementIsNotElementType()
for r in sammler:
    p = r.get_Parameter(DB.BuiltInParameter.ROOM_PHASE)
    phase = _elementname(doc.GetElement(p.AsElementId())) if p and p.AsElementId() else None
    platziert = r.Location is not None and r.Area > 0
    ebene = _elementname(r.Level) if r.Level is not None else None
    flaeche_m2 = r.Area * M2_JE_FT2                      # r.Area ist in ft²
    pos = (r.Location.Point.X, r.Location.Point.Y)       # in Fuß
    name = r.get_Parameter(DB.BuiltInParameter.ROOM_NAME).AsString()
    nummer = r.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER).AsString()
    innen = r.IsPointInRoom(DB.XYZ(x_ft, y_ft, r.Level.Elevation + 1.0 / METER_JE_FUSS))   # Prüfpunkt Ebene + 1 m

alle_ebenen = [_elementname(l) for l in DB.FilteredElementCollector(doc).OfClass(DB.Level)]
```

Schreiben: **eine** Transaktion, Werte nur bei Änderung, je Raum abgefangen, Workset-Prüfung:

```python
def _setze(param, wert, DB):
    if param is None:
        return "Parameter fehlt"
    if param.IsReadOnly:
        return "Parameter schreibgeschützt"
    if param.StorageType != DB.StorageType.String:
        return "Parameter ist kein Text"
    if (param.AsString() or "") == wert:
        return "unverändert"
    param.Set(wert)
    return "geschrieben"


def _ist_beschreibbar(rv, raum):
    doc, DB = rv.doc, rv.DB
    try:
        if doc.IsWorkshared:
            status = DB.WorksharingUtils.GetCheckoutStatus(doc, raum.Id)
            if status == DB.CheckoutStatus.OwnedByOtherUser:
                return False, "von anderem Benutzer belegt"
            ws = doc.GetWorksetTable().GetWorkset(raum.WorksetId)
            if not ws.IsEditable:
                return False, "Workset nicht bearbeitbar"
    except Exception as ex:
        return True, "Workset-Prüfung nicht möglich (%s)" % ex
    return True, ""


rv.TransactionManager.Instance.ForceCloseTransaction()      # offene Dynamo-Transaktion schließen
t = DB.Transaction(doc, "Raumstempel übertragen")
t.Start()
try:
    ...                                                      # alle Schreibvorgänge
    status = t.Commit()
    if status != DB.TransactionStatus.Committed:
        log("WARNUNG: Die Transaktion wurde nicht abgeschlossen (Status %s)." % status)
except Exception:
    t.RollBack()
    raise
```

Shared Parameter anlegen und an Räume binden (Revit 2025: `GroupTypeId`/`SpecTypeId`, nicht mehr `BuiltInParameterGroup`/`ParameterType`):

```python
raum_kat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Rooms)
tg = DB.TransactionGroup(doc, "Parameter"); tg.Start()
t = DB.Transaction(doc, "Parameter binden"); t.Start()
app.SharedParametersFilename = pfad_zur_txt
datei = app.OpenSharedParameterFile()
gruppe = datei.Groups.Create("Raumstempel")                  # oder vorhandene suchen
opt = DB.ExternalDefinitionCreationOptions("RaumOKS", DB.SpecTypeId.String.Text)
definition = gruppe.Definitions.Create(opt)
kategorien = app.Create.NewCategorySet(); kategorien.Insert(raum_kat)
bindung = app.Create.NewInstanceBinding(kategorien)
try:
    ok = doc.ParameterBindings.Insert(definition, bindung, DB.GroupTypeId.Data)
except Exception:
    ok = doc.ParameterBindings.ReInsert(definition, bindung, DB.GroupTypeId.Data)
t.Commit(); tg.Assimilate()
# immer zurücksetzen: app.SharedParametersFilename = alter_pfad
```

CAD-Verknüpfungen und Transformation (nur falls im FFB-Projekt DWG gebraucht werden):

```python
instanzen = list(DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance))
t = inst.GetTotalTransform()                  # Origin, BasisX/Y/Z in Fuß
x, y, z = (t.Origin.X + t.BasisX.X * px + t.BasisY.X * py, ...)    # Einheit VOR der Transformation in Fuß
```

## 4. Bewährte Lösungen und Stolpersteine
* **Einheiten:** Revit-API rechnet intern in **Fuß** (Länge) und **ft²** (Fläche). Wir haben bewusst mit festen
  Konstanten gearbeitet (`0.3048`, `0.09290304`) und NICHT mit `UnitUtils`/`ForgeTypeId`. Für FFB-Geometrie
  (Millimeter-Aufbauhöhen!) gilt: Eingaben in mm / 304,8 = Fuß. DWG-Meter -> Fuß = / 0,3048, danach erst transformieren.
* **Transaktionen:** `TransactionManager.Instance.ForceCloseTransaction()` vor der eigenen Transaktion; ein einziger
  Schreibvorgang in einer Transaktion; Commit-Status prüfen; bei Fehler `RollBack()`. Parameter-Anlage in einer
  `TransactionGroup` (Gruppe + Transaktion), danach `Assimilate()`.
* **`ElementId.Value`** (Revit 2024+), nicht `IntegerValue` (`_eid`-Hilfsfunktion).
* **`Element.Name` kann in CPython/Dynamo scheitern** ("property cannot be read"), Fallbacks siehe `_elementname`.
  Der Fehler wurde zuerst still verschluckt und führte zu "keine Verknüpfung gefunden". Fehler nie still schlucken.
* **Räume sind nicht nur platziert:** `r.Location is not None and r.Area > 0` prüfen; unplatzierte/nicht umschlossene
  Räume haben Fläche 0.
* **Phase:** nur Räume der Phase "Bestand" nehmen. Phasenname über `ROOM_PHASE` -> `doc.GetElement(id)`.
* **Prüfpunkt Punkt-in-Raum:** Höhe = Ebenenhöhe + 1 m, sonst trifft `IsPointInRoom` den Raum nicht.
* **Mehrere Elemente je Raum:** nie "erstes Element gewinnt" annehmen. Wir prüfen Konflikte (derselbe Raum mehrfach,
  Werte schon an anderem Raum) und melden sie in einer Prüfliste.
* **Trockenlauf zuerst:** immer erst Lauf 1 (nichts wird geschrieben), Liste prüfen, dann schreiben.
* **Mehrfach lauffähig:** Werte nur schreiben, wenn sie sich ändern (`_setze`).
* **Excel ohne Zusatzbibliothek:** XLSX per `zipfile` + XML gelesen und geschrieben. Excel macht aus `1.06` ein Datum,
  deshalb Text als Text speichern (XLSX) bzw. `="1.06"` in der CSV.
* **Excel-Datei offen:** vor dem Lesen prüfen (`~$`-Sperrdatei, `open(pfad, "r+b")` -> `PermissionError`).
* **Plausibilitätsprüfung:** Koordinaten gegen die BoundingBox der Verknüpfung prüfen; Warnung bei Abweichung.
* **Dynamo-Ausgabe:** `OUT` ist eine Liste von Textzeilen für einen Watch-Node; lange Texte in kurze Zeilen teilen.
* **Python-Node-Besonderheit:** Der Code steckt als Kopie im Node und im Repository. Änderungen in der `.py` müssen
  in den Node kopiert werden (die `.dyn` selbst enthält den Code nur als Text).
* **Test ohne Revit:** Logik in reine Funktionen auslagern, Revit-Zugriff in eine dünne Schicht. Mit einem
  nachgebildeten Revit (`tests/fake_revit.py`) lässt sich der ganze Ablauf in Python testen.

## 5. Konventionen im Modell (aus diesem Projekt, für FFB prüfen)
* **Ebenen (Revit):** `EG- OK FFB`, `1. OG - OK FFB`, `2. OG`, `3. OG`, `4. OG`, `1. UG`. Der Name enthält "OK FFB"
  (Oberkante Fertigfußboden) – die Ebene liegt also auf OK FFB. Zuordnung Code -> Ebene im Skript konfigurierbar:
  `G00=EG, G01=1. OG, G02=2. OG, G03=3. OG, G04=4. OG, U01=1. UG, U02=2. UG`.
  Die Bezeichnungen "OK RFB" und "UK Decke" haben wir in diesem Projekt NICHT gesehen. Bitte im neuen Chat sagen,
  wie sie im TGA-/Architekturmodell heißen.
* **Räume:** Phase "Bestand". Raumname unverändert aus der CAD-Stempeldatei (auch "n.v."). `ROOM_NUMBER` =
  Geschosscode + letztes OKS-Segment (`100049-004-A-G01-_15` -> `G01-_15`).
* **Parameter (Shared Parameter an Kategorie Räume, Gruppe "Daten"):** `RaumOKS` (Text), `Raumnummer_Text` (Text, mit
  Präfix "Raum-Nr. "; "n.v." bleibt erhalten). Parameterdatei: eigene Datei `Raumstempel_SharedParameters.txt` im
  Ausgabeordner, falls die Firmendatei sie nicht enthält (Eingabe 10 = Pfad der Firmendatei, noch offen).
* **Verknüpfungen:** Architekturmodell wird als Revit-Verknüpfung ins TGA-Modell eingebunden. DWG-Vorlagen heißen
  `100049_004_A_G0x.dwg`, in Revit verknüpft als `..._Bestand.dwg`.
* **Bodentypen, Wandtypen, Türfamilien:** in diesem Projekt NICHT bekannt, wir haben damit nicht gearbeitet.
  Das muss im neuen Chat neu erhoben werden (Namen der Fußbodentypen, Türfamilien, Parameter für Bodenbeläge).

## 6. Struktur des Dynamo-Graphen
1. **Code-Block** (eine Liste, Positionen 0-18): Ordner, Trockenlauf (true/false), Ebenenzuordnung, Einheit, Phasenname,
   Toleranzen, Präfix, Pfade, Ausgabeformat, Raumtags (true/false), Suchradius.
2. **Python-Script-Node** (CPython3), Eingang `IN[0]` = Ausgang des Code-Blocks.
3. **Watch-Node** am Ausgang `OUT` (Protokoll als Textzeilen).
Ausführung: **manuell** (nicht automatisch), Lauf 1 mit `true`, Lauf 2 mit `false`. Position 11 leer.
Detailbeschreibung: `ANLEITUNG.md`.

## 7. Offene Punkte / Ideen
* Raumnummer für MEP/TGA und "Solar-Computer": bewusst zurückgestellt.
* Firmen-Shared-Parameter-Datei (Eingabe 10) offen.
* Position 2 im Code-Block ist unbenutzt (nur bei Neuaufbau aufräumen).
* **FFB-Skript (neu), noch nichts umgesetzt:** Fertigfußboden je Raum aus Raumberandung (`GetBoundarySegments`),
  Ausbuchtungen in Türlaibungen (Türen aus Wandöffnungen, Laibungstiefe = Wanddicke minus Rahmen), Bodentyp je Raum
  über einen Raumparameter, Ebene OK FFB als Referenz, Höhe aus Bodentyp-Aufbau. Zu klären: Bodentypen, Türfamilien,
  Parameter für den Bodenbelag, ob Boden im Architektur- oder TGA-Modell erzeugt wird, Umgang mit Verknüpfungen.
