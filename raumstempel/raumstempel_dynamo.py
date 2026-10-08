# -*- coding: utf-8 -*-
"""
Raumstempel (AutoCAD-Datenextraktion) -> Revit-Räume
====================================================
Dynamo Python-Node (CPython 3, Revit 2025). Der Revit-Teil ist dünn und
defensiv, die gesamte Logik (CSV, Kürzung, Koordinaten, Zuordnung, Prüflisten)
steht in reinen Funktionen OHNE Revit-Abhängigkeit und wird in
tests/test_raumstempel.py getestet.

Ablauf
------
 Lauf 1 (Trockenlauf = True, Standard): Es wird NICHTS geschrieben. Das Skript
        ordnet Stempel den Räumen zu, schreibt eine Zuordnungsliste und eine
        Prüfliste (CSV, mit Element-IDs) und gibt ein Protokoll aus.
 Lauf 2 (Trockenlauf = False): Es werden nur freigegebene Zuordnungen
        geschrieben. Entweder aus der geprüften Zuordnungsliste (Spalte
        "Freigabe") oder - ohne Liste - nur Zuordnungen mit Status "sicher".

Eingaben (IN[...]) - Reihenfolge im Dynamo-Graph
------------------------------------------------
 0  Ordner mit den Stempel-CSV-Dateien                  (Text, Pflicht)
 1  Trockenlauf                                         (True/False, Standard True)
 2  Fehlende Räume anlegen (NewRoom)                    (True/False, Standard False)
 3  Ebenenzuordnung, Liste "Code=Revit-Ebenenname"      (Standard siehe unten)
 4  Zeicheneinheit der DWG: "m", "cm" oder "mm"         (Standard "m")
 5  Phasenname der Räume                                (Standard "Bestand")
 6  Schwelle "sicher" in Prozent Flächenabweichung      (Standard 5)
 7  Obergrenze für Vorschläge in Prozent                (Standard 15)
 8  Maximaler Abstand Stempel -> Raum in m (Fläche)     (Standard 10)
 9  Präfix für Raumnummer_Text                          (Standard "Raum-Nr. ")
10  Shared-Parameter-Datei der Firma (Pfad, optional)
11  Zuordnungsliste für Lauf 2 (Pfad, optional)
12  Ausgabeordner für Listen (optional, Standard <Ordner>/_Ausgabe)
13  Manuelle Verknüpfungszuordnung, Liste "Dateiname=Verknüpfungsname" (optional)
14  Parameter bei Bedarf anlegen                        (True/False, Standard True)
15  Ausschluss: Zeilen, deren Dateiname auf diesen Text endet (Standard "_Bestand",
    externe Referenzen); "-" = nichts ausschließen
16  Ausgabeformat der Listen: "xlsx" (Standard), "csv" oder "beides"

Alternativ können alle 17 Werte als EINE Liste an IN[0] übergeben werden
(ein Code-Block-Node, siehe ANLEITUNG.md). Leere Werte ("" oder null) = Standard.

Ausgabe (OUT): Liste von Textzeilen (Protokoll) für einen Watch-Node.
"""

import csv
import datetime
import glob
import io
import math
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
METER_JE_FUSS = 0.3048
M2_JE_FT2 = 0.09290304
EINHEIT_IN_METER = {"m": 1.0, "cm": 0.01, "mm": 0.001}

PARAM_OKS = "RaumOKS"
PARAM_NUMMER_TEXT = "Raumnummer_Text"
PARAM_GRUPPE = "Raumstempel"

STANDARD_EBENEN = [
    "U02=2. UG",
    "U01=1. UG",
    "G00=EG",
    "G01=1. OG",
    "G02=2. OG",
    "G03=3. OG",
    "G04=4. OG",
]

PRUEFPUNKT_HOEHE_M = 1.0

# Spaltennamen der CSV (normalisiert, siehe _norm_kopf)
_SPALTEN_ALIAS = {
    "oks": ("fmoks", "raumoks", "oks"),
    "nummer": ("fmnummer", "raumnummer"),
    "name": ("fmname", "raumname"),
    "flaeche": ("fmflaeche", "raumflaeche"),
    "x": ("positionx", "x"),
    "y": ("positiony", "y"),
    "dateiname": ("dateiname",),
    "ziel_x": ("zielx",),
    "ziel_y": ("ziely",),
}


# ---------------------------------------------------------------------------
# TEIL 1: reine Logik (ohne Revit)
# ---------------------------------------------------------------------------
class Stempel(object):
    """Ein Raumstempel aus der AutoCAD-Datenextraktion."""

    def __init__(self, oks, nummer="", name="", flaeche=None, x=None, y=None,
                 dateiname="", quelle="", zeile=0, ziel_x=None, ziel_y=None):
        self.oks = oks
        self.nummer = nummer
        self.name = name
        self.flaeche = flaeche          # m2 laut Stempel, nur zum Vergleich
        self.x = x                      # Einfügepunkt, DWG-Einheiten
        self.y = y
        self.ziel_x = ziel_x            # optional: Ankerpunkt der Verbindungslinie
        self.ziel_y = ziel_y
        self.dateiname = dateiname
        self.quelle = quelle            # CSV-Datei
        self.zeile = zeile
        self.code = geschosscode(oks)

    @property
    def punkt_x(self):
        return self.ziel_x if (self.ziel_x is not None and self.ziel_y is not None) else self.x

    @property
    def punkt_y(self):
        return self.ziel_y if (self.ziel_x is not None and self.ziel_y is not None) else self.y

    def __repr__(self):
        return "Stempel(%s, %s)" % (self.oks, self.name)


def parse_zahl(text):
    """Liest Zahlen mit Dezimalpunkt ODER Dezimalkomma. Gibt None bei Fehler."""
    if text is None:
        return None
    s = str(text).strip().replace(" ", "").replace(" ", "")
    if s == "":
        return None
    if "," in s and "." in s:
        # das letzte Trennzeichen ist das Dezimalzeichen, das andere Tausender
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def dekodiere(rohbytes):
    """UTF-8 (mit/ohne BOM), sonst Windows-1252."""
    for kodierung in ("utf-8-sig", "cp1252"):
        try:
            return rohbytes.decode(kodierung)
        except UnicodeDecodeError:
            continue
    return rohbytes.decode("latin-1")


def _norm_kopf(text):
    return re.sub(r"[^a-z0-9äöüß]", "", (text or "").lower())


def _erkenne_trenner(erste_zeile):
    zaehler = {d: erste_zeile.count(d) for d in (";", ",", "\t")}
    bester = max(zaehler, key=zaehler.get)
    return bester if zaehler[bester] > 0 else ";"


STANDARD_AUSSCHLUSS_SUFFIX = "_Bestand"


def ist_externe_referenz(dateiname, suffix=STANDARD_AUSSCHLUSS_SUFFIX):
    """True, wenn der Dateiname (ohne .dwg) auf `suffix` endet.

    Solche Zeilen sind Blöcke gleichen Namens aus externen Referenzen
    (z. B. '100049_004_A_G03_Bestand.dwg') und werden nicht verwendet.
    """
    if not suffix:
        return False
    name = re.sub(r"\.dwg$", "", (dateiname or "").strip(), flags=re.I).lower()
    return name.endswith(suffix.strip().lower())


def parse_stempel_text(text, quelle="", ausschluss_suffix=STANDARD_AUSSCHLUSS_SUFFIX,
                       ignoriert_out=None):
    """Parst CSV-Text. Gibt (stempel_liste, meldungen) zurück (siehe parse_stempel_zeilen)."""
    text = text.lstrip("﻿")
    zeilen = text.splitlines()
    if not zeilen:
        return [], ["%s: Datei ist leer." % quelle]
    leser = csv.reader(io.StringIO(text), delimiter=_erkenne_trenner(zeilen[0]))
    return parse_stempel_zeilen(list(leser), quelle, ausschluss_suffix, ignoriert_out)


def parse_stempel_zeilen(zeilen, quelle="", ausschluss_suffix=STANDARD_AUSSCHLUSS_SUFFIX,
                         ignoriert_out=None):
    """Parst eine Tabelle (Liste von Zeilen, erste Zeile = Kopf). Gibt (stempel_liste, meldungen).

    Zeilen aus externen Referenzen (Dateiname endet auf `ausschluss_suffix`)
    werden übersprungen und in einer Sammelmeldung gezählt. `ignoriert_out`
    (dict, optional) sammelt je Geschosscode die Zahl der ignorierten Zeilen.
    """
    meldungen = []
    stempel = []
    ignoriert = {}
    if not zeilen or not any((k or "").strip() for k in zeilen[0]):
        return [], ["%s: keine Kopfzeile." % quelle]
    kopf = zeilen[0]

    index = {}
    normiert = [_norm_kopf(k) for k in kopf]
    for schluessel, alias in _SPALTEN_ALIAS.items():
        for a in alias:
            if a in normiert:
                index[schluessel] = normiert.index(a)
                break
    fehlend = [k for k in ("oks", "x", "y") if k not in index]
    if fehlend:
        return [], ["%s: Pflichtspalte(n) nicht gefunden: %s (Kopfzeile: %s)"
                    % (quelle, ", ".join(fehlend), ", ".join(kopf))]

    def feld(zeile, schluessel):
        i = index.get(schluessel)
        if i is None or i >= len(zeile):
            return ""
        return zeile[i]

    for nr, zeile in enumerate(zeilen[1:], start=2):
        if not any((z or "").strip() for z in zeile):
            continue
        oks = re.sub(r"\s+", "", feld(zeile, "oks"))
        if not oks:
            meldungen.append("%s Zeile %d: keine OKS, übersprungen." % (quelle, nr))
            continue
        dateiname = feld(zeile, "dateiname").strip()
        if ist_externe_referenz(dateiname, ausschluss_suffix):
            ignoriert[dateiname] = ignoriert.get(dateiname, 0) + 1
            if ignoriert_out is not None:
                code = geschosscode(oks)
                ignoriert_out[code] = ignoriert_out.get(code, 0) + 1
            continue
        x = parse_zahl(feld(zeile, "x"))
        y = parse_zahl(feld(zeile, "y"))
        if x is None or y is None:
            meldungen.append("%s Zeile %d (%s): Position X/Y nicht lesbar, übersprungen."
                             % (quelle, nr, oks))
            continue
        stempel.append(Stempel(
            oks=oks,
            nummer=feld(zeile, "nummer").strip(),
            name=feld(zeile, "name").strip(),
            flaeche=parse_zahl(feld(zeile, "flaeche")),
            x=x, y=y,
            ziel_x=parse_zahl(feld(zeile, "ziel_x")),
            ziel_y=parse_zahl(feld(zeile, "ziel_y")),
            dateiname=dateiname,
            quelle=quelle, zeile=nr))
    if ignoriert:
        meldungen.append("%s: %d Zeilen aus externen Referenzen ignoriert (%s)."
                         % (quelle, sum(ignoriert.values()),
                            ", ".join("%s: %d" % (k, v) for k, v in sorted(ignoriert.items()))))
    return stempel, meldungen


AUSGABE_PRAEFIXE = ("zuordnungsliste_", "pruefliste_")


def lese_stempel_ordner(ordner, ausschluss_suffix=STANDARD_AUSSCHLUSS_SUFFIX, ignoriert_out=None):
    """Liest alle *.csv und *.xlsx im Ordner (nicht rekursiv). Gibt (stempel, meldungen).

    Ausgabelisten des Skripts (Zuordnungsliste_*, Pruefliste_*) und Excel-Sperrdateien (~$...)
    werden übersprungen.
    """
    dateien = sorted(glob.glob(os.path.join(ordner, "*.csv")) + glob.glob(os.path.join(ordner, "*.xlsx")))
    dateien = [d for d in dateien
               if not os.path.basename(d).startswith("~$")
               and not os.path.basename(d).lower().startswith(AUSGABE_PRAEFIXE)]
    alle, meldungen = [], []
    if not dateien:
        meldungen.append("Keine CSV-/Excel-Dateien im Ordner gefunden: %s" % ordner)
    for pfad in dateien:
        name = os.path.basename(pfad)
        try:
            zeilen = lese_tabelle(pfad)
        except Exception as ex:
            meldungen.append("%s: Datei konnte nicht gelesen werden (%s)." % (name, ex))
            continue
        st, mel = parse_stempel_zeilen(zeilen, quelle=name, ausschluss_suffix=ausschluss_suffix,
                                       ignoriert_out=ignoriert_out)
        alle.extend(st)
        meldungen.extend(mel)
    return alle, meldungen


def geschosscode(oks):
    """'100049-004-A-G03-_01' -> 'G03'; None, wenn kein Code gefunden."""
    m = re.search(r"(?:^|-)([GU]\d{2})(?:-|$)", re.sub(r"\s+", "", oks or ""), re.I)
    return m.group(1).upper() if m else None


def kurz_nummer(oks, modus="segmente", laenge=7):
    """Revit-Raumnummer aus der OKS. Gibt (nummer, fehlertext) zurück.

    modus "segmente": Geschosscode + letztes Segment ('...-G01-_15' -> 'G01-_15'),
                      unabhängig von der Anzahl der Ziffern.
    modus "laenge":   die letzten `laenge` Zeichen.
    """
    s = re.sub(r"\s+", "", oks or "")
    if modus == "laenge":
        if len(s) < laenge:
            return None, "OKS kürzer als %d Zeichen: '%s'" % (laenge, s)
        return s[-laenge:], None
    teile = s.split("-")
    if len(teile) < 2:
        return None, "OKS hat zu wenige Segmente: '%s'" % s
    nummer = "-".join(teile[-2:])
    if not re.match(r"^[GU]\d{2}-.+$", nummer, re.I):
        return None, "OKS-Ende hat nicht das Format 'G00-...': '%s'" % s
    return nummer, None


def bereinige_doppelte(stempel):
    """Trennt Doppelte nach OKS.

    Identische Stempel (gleicher Name, gleiche Nummer/Fläche/Position - z. B. dieselbe
    CSV zweimal im Ordner) werden auf einen reduziert. Widersprüchliche Stempel mit
    gleicher OKS werden NICHT verarbeitet.
    Rückgabe: (liste, identisch_entfernt, konflikte)
      liste               Stempel ohne Doppelte und ohne Konflikte
      identisch_entfernt  Liste der entfernten identischen Kopien
      konflikte           dict oks -> [Stempel, ...]
    """
    gruppen, reihenfolge = {}, []
    for s in stempel:
        if s.oks not in gruppen:
            gruppen[s.oks] = []
            reihenfolge.append(s.oks)
        gruppen[s.oks].append(s)

    def signatur(s):
        return (s.name.strip(), s.nummer.strip(), s.flaeche, round(s.x, 3), round(s.y, 3))

    liste, entfernt, konflikte = [], [], {}
    for oks in reihenfolge:
        g = gruppen[oks]
        if len({signatur(s) for s in g}) == 1:
            liste.append(g[0])
            entfernt.extend(g[1:])
        else:
            konflikte[oks] = g
    return liste, entfernt, konflikte


def finde_doppelte(werte):
    """werte: dict schluessel -> wert. Gibt dict wert -> [schluessel] mit Duplikaten."""
    gruppen = {}
    for k, v in werte.items():
        gruppen.setdefault(v, []).append(k)
    return {v: ks for v, ks in gruppen.items() if len(ks) > 1}


# --- Ebenen ----------------------------------------------------------------
def norm_ebenenname(name):
    return re.sub(r"[\s\.]", "", name or "").lower()


def parse_ebenen_zuordnung(zeilen):
    """['G01=1. OG', ...] -> {'G01': '1. OG'}"""
    ergebnis = {}
    for z in zeilen:
        if "=" not in str(z):
            continue
        code, name = str(z).split("=", 1)
        ergebnis[code.strip().upper()] = name.strip()
    return ergebnis


def pruefe_ebenen(codes_anzahl, zuordnung, revit_ebenen, raeume_je_ebene):
    """Beurteilt je Geschosscode, ob Stempel verarbeitet werden dürfen.

    codes_anzahl:    {code oder None: Anzahl Stempel}
    zuordnung:       {code: Soll-Ebenenname}
    revit_ebenen:    Liste der Revit-Ebenennamen
    raeume_je_ebene: {Revit-Ebenenname: Anzahl Räume (Phase, platziert)}
    Rückgabe: Liste von dict(code, anzahl, ebene, status, ok)
    """
    nach_norm = {norm_ebenenname(n): n for n in revit_ebenen}
    ergebnis = []
    for code in sorted(codes_anzahl, key=lambda c: (c is None, c or "")):
        anzahl = codes_anzahl[code]
        eintrag = {"code": code, "anzahl": anzahl, "ebene": None, "ok": False}
        if code is None:
            eintrag["status"] = "Kein Geschosscode in der OKS erkennbar"
        elif code not in zuordnung:
            eintrag["status"] = "Geschosscode nicht in der Ebenenzuordnung"
        else:
            revit_name = nach_norm.get(norm_ebenenname(zuordnung[code]))
            if revit_name is None:
                eintrag["status"] = "Ebene '%s' nicht im Revit-Modell" % zuordnung[code]
            else:
                eintrag["ebene"] = revit_name
                n = raeume_je_ebene.get(revit_name, 0)
                if n == 0:
                    eintrag["status"] = "Ebene ohne Räume (nicht modelliert) - Stempel werden nicht verarbeitet"
                else:
                    eintrag["status"] = "ok (%d Räume)" % n
                    eintrag["ok"] = True
        ergebnis.append(eintrag)
    return ergebnis


# --- DWG-Verknüpfung finden --------------------------------------------------
def norm_dwgname(name):
    """'U:/x/100049_004_A_G03_Bestand.DWG' -> '100049_004_a_g03_bestand'"""
    n = re.split(r"[\\/]", str(name or "").strip())[-1].lower()
    return re.sub(r"\.dwg$", "", n)


def kandidaten_linknamen(dateiname, suffix=STANDARD_AUSSCHLUSS_SUFFIX, manuell=None):
    """Namen der Revit-Verknüpfung, die für einen Stempel-Dateinamen infrage kommen.

    Die Stempel stammen aus '<Geschoss>.dwg' (nur Räume, Geschosse, Stempel). In Revit
    ist aber die zugehörige '<Geschoss>_Bestand.dwg' verknüpft (Wände, Türen, ...).
    Beide Zeichnungen haben denselben Nullpunkt und dieselben Einheiten, deshalb wird
    ihre Verknüpfung für die Koordinaten benutzt. Reihenfolge: manuelle Zuordnung,
    sonst '<Name><Suffix>', sonst '<Name>'.
    """
    basis = norm_dwgname(dateiname)
    manuell = manuell or {}
    if basis in manuell:
        return [manuell[basis]]
    namen = []
    if suffix and suffix.strip():
        namen.append(basis + suffix.strip().lower())
    namen.append(basis)
    return namen


def waehle_verknuepfung(dateinamen, verfuegbare, suffix=STANDARD_AUSSCHLUSS_SUFFIX, manuell=None):
    """Wählt den Namen der Revit-Verknüpfung. Gibt (name oder None, hinweis) zurück.

    dateinamen: Dateinamen der Stempel einer Ebene; verfuegbare: Namen der DWG-Verknüpfungen
    im Projekt (normalisiert oder roh).
    """
    vorhanden = {norm_dwgname(v): v for v in verfuegbare}
    for dn in sorted({norm_dwgname(d) for d in dateinamen if d}):
        for kandidat in kandidaten_linknamen(dn, suffix, manuell):
            k = norm_dwgname(kandidat)
            if k in vorhanden:
                hinweis = ""
                if k != dn:
                    hinweis = "Stempel aus '%s', Koordinaten von Verknüpfung '%s'" % (dn, vorhanden[k])
                return vorhanden[k], hinweis
    return None, ""


# --- Koordinaten -----------------------------------------------------------
def einheit_in_fuss(einheit):
    """Faktor DWG-Einheit -> Revit-Fuß."""
    try:
        return EINHEIT_IN_METER[(einheit or "m").strip().lower()] / METER_JE_FUSS
    except KeyError:
        raise ValueError("Unbekannte Einheit '%s' (erlaubt: m, cm, mm)" % einheit)


def dwg_nach_revit(x, y, einheit, trafo):
    """DWG-Weltkoordinate -> Revit-Koordinate in Fuß.

    trafo: dict(origin=(x,y,z), basis_x=(..), basis_y=(..), basis_z=(..)) = die
    Daten von ImportInstance.GetTotalTransform(). Die Einheiten-Umrechnung
    erfolgt VOR der Transformation (Revit-API rechnet intern immer in Fuß).
    """
    f = einheit_in_fuss(einheit)
    px, py, pz = x * f, y * f, 0.0
    o, bx, by, bz = trafo["origin"], trafo["basis_x"], trafo["basis_y"], trafo["basis_z"]
    return tuple(o[i] + bx[i] * px + by[i] * py + bz[i] * pz for i in range(3))


def pruefpunkt_hoehe_fuss(ebenenhoehe_fuss):
    """Höhe des Prüfpunkts: Ebenenhöhe + ca. 1 m (nicht die Höhe der Verknüpfung)."""
    return ebenenhoehe_fuss + PRUEFPUNKT_HOEHE_M / METER_JE_FUSS


# --- Zuordnung -------------------------------------------------------------
class Zuordnung(object):
    def __init__(self, stempel_idx, raum_id, methode, abweichung, status, bemerkung=""):
        self.stempel_idx = stempel_idx
        self.raum_id = raum_id
        self.methode = methode          # "Position", "Position+Fläche", "Fläche"
        self.abweichung = abweichung    # Prozent oder None
        self.status = status            # "sicher" | "unsicher"
        self.bemerkung = bemerkung


def abweichung_prozent(stempel_flaeche, raum_flaeche):
    if stempel_flaeche is None or raum_flaeche is None or raum_flaeche <= 0:
        return None
    return abs(stempel_flaeche - raum_flaeche) / raum_flaeche * 100.0


def ordne_zu(stempel, raeume, treffer, positionen=None,
             tol_sicher=5.0, tol_max=15.0, max_abstand=None):
    """Ordnet die Stempel EINER Ebene den Räumen derselben Ebene zu.

    stempel:   Liste von Stempel (Index = Stempel-Index)
    raeume:    dict raum_id -> dict(flaeche=m2, pos=(x,y) oder None)
    treffer:   dict stempel_idx -> [raum_id, ...] (Punkt-in-Raum-Test)
    positionen: dict stempel_idx -> (x,y) in derselben Einheit wie raeume[..]['pos']
    max_abstand: größter erlaubter Abstand bei reiner Flächenzuordnung (None = aus)

    Stufen:
      1. Position: in jedem Raum gewinnt der Stempel mit der besten
         Flächenübereinstimmung (<= tol_max). Weitere Stempel im Raum werden
         freigegeben (Fremdstempel, z. B. Stempel anderer Räume im Treppenhaus).
      2. Fläche: freigegebene Stempel und Räume ohne Stempel werden über die
         Fläche (bei Gleichstand über den Abstand) eindeutig gepaart.
      3. Position (Fläche weicht ab): Räume, die weiter leer sind, aber einen
         freien Stempel enthalten, bekommen diesen als Vorschlag "unsicher".
    "sicher" = Abweichung <= tol_sicher und keine gleichwertige Alternative.
    Gibt dict: zuordnungen, stempel_ohne_raum, raeume_ohne_stempel, raeume_mehrfach.
    """
    positionen = positionen or {}
    zuordnungen = []
    stempel_zu = {}                 # stempel_idx -> Zuordnung
    raum_zu = {}                    # raum_id -> Zuordnung

    def abw(si, rid):
        return abweichung_prozent(stempel[si].flaeche, raeume[rid]["flaeche"])

    def vergeben(z):
        zuordnungen.append(z)
        stempel_zu[z.stempel_idx] = z
        raum_zu[z.raum_id] = z

    treffer_je_raum = {}
    for si, rids in treffer.items():
        for rid in rids:
            if rid in raeume:
                treffer_je_raum.setdefault(rid, []).append(si)
    raeume_mehrfach = {rid: sorted(sis) for rid, sis in treffer_je_raum.items() if len(sis) > 1}

    # Stufe 1: Position (+ Fläche, wenn mehrere Stempel im Raum)
    for rid in sorted(treffer_je_raum):
        sis = sorted(treffer_je_raum[rid])
        kandidaten = []
        for si in sis:
            if si in stempel_zu:
                continue
            a = abw(si, rid)
            if a is not None and a <= tol_max:
                kandidaten.append((a, si))
        if not kandidaten:
            continue
        kandidaten.sort()
        beste_abw, bester = kandidaten[0]
        gleichwertig = [k for k in kandidaten if k[0] <= tol_sicher]
        eindeutig = beste_abw <= tol_sicher and len(gleichwertig) == 1
        bem = ""
        if len(sis) > 1:
            bem = "%d Stempel im Raum, Zuordnung über Fläche" % len(sis)
        elif beste_abw > tol_sicher:
            bem = "Fläche weicht um %.1f %% ab" % beste_abw
        if len(gleichwertig) > 1:
            bem = "mehrere Stempel mit passender Fläche im Raum"
        vergeben(Zuordnung(bester, rid, "Position+Fläche" if len(sis) > 1 else "Position",
                           beste_abw, "sicher" if eindeutig else "unsicher", bem))

    # Stufe 2: Fläche für die übrigen
    freie_stempel = [i for i in range(len(stempel)) if i not in stempel_zu]
    freie_raeume = [rid for rid in raeume if rid not in raum_zu]
    paare = []
    for si in freie_stempel:
        for rid in freie_raeume:
            a = abw(si, rid)
            if a is None or a > tol_max:
                continue
            d = None
            p, q = positionen.get(si), raeume[rid].get("pos")
            if p is not None and q is not None:
                d = math.hypot(p[0] - q[0], p[1] - q[1])
            if max_abstand is not None and d is not None and d > max_abstand:
                continue
            paare.append((a, d, si, rid))

    def schluessel(p):
        a, d, si, rid = p
        d = d if d is not None else 1e9
        return (0, d, a, si, rid) if a <= tol_sicher else (1, a, d, si, rid)

    for a, d, si, rid in sorted(paare, key=schluessel):
        if si in stempel_zu or rid in raum_zu:
            continue
        # Eindeutigkeit wird am ganzen Kandidatenfeld gemessen, nicht an der
        # Reihenfolge der Vergabe
        gleichwertig_stempel = [p for p in paare if p[2] == si and p[3] != rid and p[0] <= tol_sicher]
        gleichwertig_raum = [p for p in paare if p[3] == rid and p[2] != si and p[0] <= tol_sicher]
        eindeutig = a <= tol_sicher and not gleichwertig_stempel and not gleichwertig_raum
        bem = "Zuordnung nur über Fläche"
        if a > tol_sicher:
            bem += ", Abweichung %.1f %%" % a
        elif not eindeutig:
            bem += ", mehrere Räume/Stempel mit ähnlicher Fläche (Abstand entscheidet)"
        vergeben(Zuordnung(si, rid, "Fläche", a, "sicher" if eindeutig else "unsicher", bem))

    # Stufe 3: leere Räume mit freiem Stempel am Ort, Fläche weicht stark ab
    for rid in sorted(treffer_je_raum):
        if rid in raum_zu:
            continue
        frei = [si for si in treffer_je_raum[rid] if si not in stempel_zu]
        if not frei:
            continue

        def sortwert(si):
            a = abw(si, rid)
            return (a is None, a if a is not None else 0.0, si)
        si = sorted(frei, key=sortwert)[0]
        a = abw(si, rid)
        bem = ("Stempel liegt im Raum, Fläche weicht um %.1f %% ab" % a) if a is not None \
            else "Stempel liegt im Raum, keine Stempelfläche zum Vergleich"
        vergeben(Zuordnung(si, rid, "Position", a, "unsicher", bem))

    return {
        "zuordnungen": zuordnungen,
        "stempel_ohne_raum": [i for i in range(len(stempel)) if i not in stempel_zu],
        "raeume_ohne_stempel": [rid for rid in raeume if rid not in raum_zu],
        "raeume_mehrfach": raeume_mehrfach,
    }


# --- Schreibvorgaben --------------------------------------------------------
def schreibvorgaben(stempel, praefix="Raum-Nr. ", modus="segmente", laenge=7):
    """Zielwerte für einen Raum. Gibt (werte, fehler) zurück.

    werte: dict name, oks, nummer_text, nummer (nummer None bei Fehler)
    """
    nummer, fehler = kurz_nummer(stempel.oks, modus, laenge)
    nr_text = stempel.nummer.strip()
    return {
        "name": stempel.name.strip(),
        "oks": stempel.oks,
        "nummer_text": (praefix + nr_text) if nr_text else "",
        "nummer": nummer,
    }, fehler


def aenderungen(ist, soll):
    """Gibt nur die Felder zurück, deren Wert sich ändert (Wiederholbarkeit)."""
    return {k: v for k, v in soll.items() if v is not None and ist.get(k) != v}


# --- CSV-Ausgabe / Zuordnungsliste ------------------------------------------
ZUORDNUNG_SPALTEN = ["Ebene", "OKS", "OKS_Tausch", "Stempel_Nummer", "Stempel_Name", "Stempel_Flaeche",
                     "Raum_ID", "Raum_Nummer_alt", "Raum_Name_alt", "Raum_Flaeche",
                     "Abweichung_Prozent", "Methode", "Status", "Freigabe", "Bemerkung"]
PRUEF_SPALTEN = ["Kategorie", "Ebene", "OKS", "Stempel_Nummer", "Name", "Raum_ID", "Detail"]
ZUORDNUNG_TEXTSPALTEN = ("Stempel_Nummer", "Raum_Nummer_alt")
PRUEF_TEXTSPALTEN = ("Stempel_Nummer",)
ZUORDNUNG_ZAHLENFORMAT = {"Stempel_Flaeche": 2, "Raum_Flaeche": 2, "Abweichung_Prozent": 1}


def schreibe_csv(pfad, spalten, zeilen):
    """Semikolon, UTF-8 mit BOM (öffnet in deutschem Excel korrekt)."""
    ordner = os.path.dirname(pfad)
    if ordner and not os.path.isdir(ordner):
        os.makedirs(ordner)
    with io.open(pfad, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(spalten)
        for z in zeilen:
            w.writerow([("" if z.get(s) is None else z.get(s)) for s in spalten])


# --- XLSX (nur Standardbibliothek: zipfile + XML) -----------------------------------
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML_UNGUELTIG = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

# Zellformate: Index in <cellXfs> (siehe _STYLES_XML)
_STIL = {(False, 0): 0, (False, 2): 2, (False, 3): 3, (True, 0): 4, (True, 2): 5, (True, 3): 6}

_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="' + _NS_MAIN + '">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0"/></numFmts>'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="4"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/><bgColor indexed="64"/></patternFill></fill>'
    '</fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="7">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>'
    '<xf numFmtId="2" fontId="0" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1"/>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')


def _spaltenbuchstabe(i):
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'"""
    name, i = "", i + 1
    while i:
        i, rest = divmod(i - 1, 26)
        name = chr(65 + rest) + name
    return name


def _spaltenindex(buchstaben):
    """'A' -> 0, 'AA' -> 26"""
    n = 0
    for c in buchstaben.upper():
        n = n * 26 + (ord(c) - 64)
    return n - 1


def _xml_text(wert):
    return _xml_escape(_XML_UNGUELTIG.sub("", str(wert)), {'"': "&quot;"})


def schreibe_xlsx(pfad, blattname, spalten, zeilen, zahlenformat=None, freigabe_spalte=None,
                  markiere=None):
    """Schreibt eine einfache .xlsx (ohne Zusatzbibliothek).

    Texte werden als Text gespeichert (Excel wandelt '1.06' NICHT in ein Datum um), Zahlen als
    Zahlen. Kopfzeile fett/farbig, fixiert, mit Autofilter, Spaltenbreiten angepasst.
    zahlenformat: {Spalte: Dezimalstellen (1 oder 2)} für Gleitkommawerte
    freigabe_spalte: Name einer Spalte, die eine Auswahlliste J/N bekommt
    markiere: Funktion zeile -> bool; markierte Zeilen werden hellgelb hinterlegt
    """
    zahlenformat = zahlenformat or {}
    breiten = [max(len(str(sp)), 6) for sp in spalten]
    zeilen_xml = ['<row r="1">%s</row>' % "".join(
        '<c r="%s1" s="1" t="inlineStr"><is><t>%s</t></is></c>' % (_spaltenbuchstabe(i), _xml_text(sp))
        for i, sp in enumerate(spalten))]
    for nr, z in enumerate(zeilen, start=2):
        mark = bool(markiere and markiere(z))
        zellen = []
        for i, sp in enumerate(spalten):
            wert = z.get(sp)
            if wert is None or wert == "":
                continue
            ref = "%s%d" % (_spaltenbuchstabe(i), nr)
            if isinstance(wert, bool):
                zellen.append('<c r="%s" s="%d" t="b"><v>%d</v></c>' % (ref, _STIL[(mark, 0)], int(wert)))
                breiten[i] = max(breiten[i], 5)
            elif isinstance(wert, (int, float)):
                if isinstance(wert, float) and (math.isnan(wert) or math.isinf(wert)):
                    continue
                basis = {2: 2, 1: 3}.get(zahlenformat.get(sp)) if isinstance(wert, float) else None
                text = repr(wert) if isinstance(wert, float) else str(wert)
                zellen.append('<c r="%s" s="%d"><v>%s</v></c>' % (ref, _STIL[(mark, basis or 0)], text))
                breiten[i] = max(breiten[i], len(text))
            else:
                text = str(wert)
                zellen.append('<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                              % (ref, _STIL[(mark, 0)], _xml_text(text)))
                breiten[i] = max(breiten[i], max(len(t) for t in text.splitlines() or [""]))
        zeilen_xml.append('<row r="%d">%s</row>' % (nr, "".join(zellen)))

    letzte_spalte = _spaltenbuchstabe(max(len(spalten) - 1, 0))
    letzte_zeile = max(len(zeilen) + 1, 2)
    cols = "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, min(b + 2, 60))
                   for i, b in enumerate(breiten))
    validierung = ""
    if freigabe_spalte in spalten:
        b = _spaltenbuchstabe(spalten.index(freigabe_spalte))
        validierung = ('<dataValidations count="1"><dataValidation type="list" allowBlank="1" '
                       'showErrorMessage="1" sqref="%s2:%s%d"><formula1>"J,N"</formula1>'
                       '</dataValidation></dataValidations>' % (b, b, letzte_zeile + 200))
    blatt = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="' + _NS_MAIN + '">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
             'activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" '
             'sqref="A2"/></sheetView></sheetViews>'
             '<sheetFormatPr defaultRowHeight="15"/>'
             '<cols>' + cols + '</cols>'
             '<sheetData>' + "".join(zeilen_xml) + '</sheetData>'
             '<autoFilter ref="A1:%s%d"/>' % (letzte_spalte, letzte_zeile) + validierung +
             '</worksheet>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    # Blattname: höchstens 31 Zeichen, ohne : \ / ? * [ ]
    blattname = re.sub(r"[:\\/?*\[\]]", "_", _XML_UNGUELTIG.sub("", str(blattname)))[:31].strip("'") or "Blatt1"
    filter_bereich = "'%s'!$A$1:$%s$%d" % (blattname.replace("'", "''"), letzte_spalte, letzte_zeile)
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="' + _NS_MAIN + '" xmlns:r="' + _NS_REL + '">'
                '<sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets>'
                '<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">%s'
                '</definedName></definedNames></workbook>'
                % (_xml_text(blattname), _xml_text(filter_bereich)))
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               '</Relationships>')
    ordner = os.path.dirname(pfad)
    if ordner and not os.path.isdir(ordner):
        os.makedirs(ordner)
    with zipfile.ZipFile(pfad, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", _STYLES_XML)
        z.writestr("xl/worksheets/sheet1.xml", blatt)


def _zahl_als_text(v):
    """'3258026' bzw. '3258026.0' -> '3258026'; '17.64' bleibt '17.64'."""
    try:
        f = float(v)
    except ValueError:
        return v
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return repr(f)


def lese_xlsx(pfad, blatt=None):
    """Liest ein Tabellenblatt (Standard: das erste) als Liste von Zeilen (Listen von Strings).

    Funktioniert mit von Excel gespeicherten Dateien (gemeinsame Zeichenketten, leere Zellen)
    und mit den vom Skript geschriebenen. Zahlen werden als Text übergeben ('3258026').
    """
    ns = {"m": _NS_MAIN, "r": _NS_REL}
    with zipfile.ZipFile(pfad) as z:
        namen = set(z.namelist())
        gemeinsam = []
        if "xl/sharedStrings.xml" in namen:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                teile = [t.text or "" for t in si.findall("m:t", ns)]
                teile += [t.text or "" for t in si.findall("m:r/m:t", ns)]
                gemeinsam.append("".join(teile))
        blattpfad = "xl/worksheets/sheet1.xml"
        try:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            rel = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            ziele = {r.get("Id"): r.get("Target") for r in rel}
            sheets = wb.findall("m:sheets/m:sheet", ns)
            wahl = [sh for sh in sheets if blatt is None or sh.get("name") == blatt] or sheets
            ziel = ziele.get(wahl[0].get("{%s}id" % _NS_REL))
            if ziel:
                blattpfad = ziel.lstrip("/") if ziel.startswith("/") else "xl/" + ziel
        except (KeyError, IndexError, ET.ParseError):
            pass
        wurzel = ET.fromstring(z.read(blattpfad))
    zeilen = []
    for row in wurzel.findall("m:sheetData/m:row", ns):
        werte = {}
        for c in row.findall("m:c", ns):
            ref = c.get("r") or ""
            m = re.match(r"([A-Za-z]+)", ref)
            idx = _spaltenindex(m.group(1)) if m else len(werte)
            art = c.get("t")
            v = c.find("m:v", ns)
            if art == "s" and v is not None:
                text = gemeinsam[int(v.text)]
            elif art == "inlineStr":
                text = "".join((t.text or "") for t in c.iter("{%s}t" % _NS_MAIN))
            elif art == "b":
                text = "TRUE" if (v is not None and v.text == "1") else "FALSE"
            elif art == "e":
                text = ""
            elif v is None or v.text is None:
                text = ""
            elif art in ("str",):
                text = v.text
            else:
                text = _zahl_als_text(v.text)
            werte[idx] = text
        zeile = [werte.get(i, "") for i in range(max(werte) + 1)] if werte else []
        try:
            nr = int(row.get("r"))
            while len(zeilen) < nr - 1:
                zeilen.append([])
        except (TypeError, ValueError):
            pass
        zeilen.append(zeile)
    while zeilen and not any(zeilen[-1]):
        zeilen.pop()
    return zeilen


def lese_tabelle(pfad):
    """Liest .xlsx/.xlsm oder .csv als Liste von Zeilen (Listen von Strings), erste Zeile = Kopf."""
    if pfad.lower().endswith((".xlsx", ".xlsm")):
        return lese_xlsx(pfad)
    with open(pfad, "rb") as f:
        text = dekodiere(f.read()).lstrip("﻿")
    zeilen = text.splitlines()
    if not zeilen:
        return []
    return list(csv.reader(io.StringIO(text), delimiter=_erkenne_trenner(zeilen[0])))


def csv_zeilen(zeilen, textspalten=(), zahlenformat=None):
    """Bereitet Zeilen für die CSV-Ausgabe auf: Nummern als Excel-Text, Zahlen mit Dezimalkomma."""
    zahlenformat = zahlenformat or {}
    ergebnis = []
    for z in zeilen:
        n = dict(z)
        for sp in textspalten:
            if n.get(sp) is not None:
                n[sp] = excel_text(n[sp])
        for sp, dez in zahlenformat.items():
            if isinstance(n.get(sp), float):
                n[sp] = fmt_dezimal(n[sp], dez)
        ergebnis.append(n)
    return ergebnis


def excel_text(wert):
    """Schützt Zahlen-/Datumsähnliches (z. B. Raumnummer '1.06') vor der Umwandlung in ein Datum.

    Schreibt `="1.06"`; Excel zeigt dann den Text 1.06. Normaler Text bleibt unverändert.
    """
    w = "" if wert is None else str(wert)
    if re.match(r"^\d[\d.,/\-: ]*$", w.strip()):
        return '="%s"' % w.strip()
    return w


def zelle_text(wert):
    """Gegenstück zu excel_text: '="1.06"' -> '1.06'."""
    w = "" if wert is None else str(wert).strip()
    m = re.match(r'^="(.*)"$', w)
    return m.group(1) if m else w


def fmt_dezimal(zahl, stellen=2):
    """Zahl mit Dezimalkomma (deutsches Excel erkennt sie als Zahl); None -> ''."""
    if zahl is None:
        return ""
    return ("%.*f" % (stellen, zahl)).replace(".", ",")


def ist_freigabe(wert):
    return str(wert or "").strip().lower() in ("j", "ja", "x", "1", "true", "y", "yes")


def lese_zuordnungsliste(pfad):
    """Liest die (geprüfte) Zuordnungsliste (.xlsx oder .csv). Gibt (zeilen, meldungen)."""
    tabelle = lese_tabelle(pfad)
    if not tabelle:
        return [], ["Zuordnungsliste ist leer."]
    kopf = [(k or "").strip() for k in tabelle[0]]
    ergebnis, meldungen = [], []
    for nr, werte in enumerate(tabelle[1:], start=2):
        if not any((w or "").strip() for w in werte):
            continue
        z = {k: zelle_text(werte[i] if i < len(werte) else "") for i, k in enumerate(kopf) if k}
        try:
            rid = int(_zahl_als_text(str(z.get("Raum_ID", "")).strip()))
        except ValueError:
            if ist_freigabe(z.get("Freigabe")):
                meldungen.append("Zuordnungsliste Zeile %d (%s): freigegeben, aber Raum_ID fehlt."
                                 % (nr, z.get("OKS")))
            continue
        z["Raum_ID"] = rid
        z["_freigabe"] = ist_freigabe(z.get("Freigabe"))
        ergebnis.append(z)
    return ergebnis, meldungen


def geschoss_passt(oks, raum_ebene, zuordnung):
    """Gehört der Stempel (über den Geschosscode der OKS) zur Ebene des Raums?

    Gibt True/False zurück, None wenn nicht beurteilbar (kein Code, Code nicht in der
    Ebenenzuordnung oder Ebene unbekannt).
    """
    code = geschosscode(oks)
    if code is None or code not in zuordnung or not raum_ebene:
        return None
    return norm_ebenenname(zuordnung[code]) == norm_ebenenname(raum_ebene)


def pruefe_schreibkonflikte(jobs, raeume):
    """Prüft vor dem Schreiben (Lauf 2), ob die Liste zu doppelten Werten führen würde.

    jobs:   Liste von dict(raum_id, oks, nummer), eine je freigegebener Zeile
    raeume: dict raum_id -> dict(oks, nummer) mit dem IST-Zustand der Revit-Räume
    Der Endzustand wird simuliert: Räume, die in dieser Liste neu beschrieben werden, haben
    danach die neuen Werte (so sind auch vertauschte Zuordnungen möglich). Rückgabe:
      mehrfach  {raum_id: [job-index, ...]}  derselbe Raum steht mehrfach in der Liste
      oks       {job-index: [raum_id, ...]}  OKS stünde danach an mehreren Räumen
      nummer    {job-index: [raum_id, ...]}  Raumnummer stünde danach an mehreren Räumen
    Zeilen aus `mehrfach` und `oks` dürfen nicht geschrieben werden, bei `nummer` nur die Nummer.
    """
    je_raum = {}
    for i, j in enumerate(jobs):
        je_raum.setdefault(j["raum_id"], []).append(i)
    mehrfach = {rid: idx for rid, idx in je_raum.items() if len(idx) > 1}
    gueltig = [i for i, j in enumerate(jobs) if j["raum_id"] not in mehrfach]
    neu = {jobs[i]["raum_id"]: jobs[i] for i in gueltig}
    alle = set(raeume) | set(neu)
    oks_konflikt, nummer_konflikt = {}, {}
    for i in gueltig:
        j = jobs[i]
        gleiche_oks, gleiche_nr = [], []
        for rid in sorted(alle):
            if rid == j["raum_id"]:
                continue
            ist = raeume.get(rid, {})
            if rid in neu:
                oks_dort = neu[rid]["oks"]
                nr_dort = neu[rid]["nummer"] or ist.get("nummer", "")
            else:
                oks_dort, nr_dort = ist.get("oks", ""), ist.get("nummer", "")
            if j["oks"] and oks_dort == j["oks"]:
                gleiche_oks.append(rid)
            if j["nummer"] and nr_dort == j["nummer"]:
                gleiche_nr.append(rid)
        if gleiche_oks:
            oks_konflikt[i] = gleiche_oks
        if gleiche_nr:
            nummer_konflikt[i] = gleiche_nr
    return {"mehrfach": mehrfach, "oks": oks_konflikt, "nummer": nummer_konflikt}


def stempel_fuer_zeile(zeile, stempel_nach_oks):
    """Stempel zu einer Zeile der Zuordnungsliste.

    Gültige OKS: die Spalte `OKS_Tausch`, wenn sie gefüllt ist, sonst die Spalte `OKS`.
    Quelle der Wahrheit für Name und Nummer sind die ORIGINAL-CSV-Dateien (nicht die in Excel
    bearbeitete Liste: Excel kann '1.06' in ein Datum verwandeln). Gibt (stempel, quelle) zurück:
      'csv'    OKS in den Stempeldaten gefunden
      'liste'  OKS dort nicht gefunden, kein Tausch: Werte der Listenzeile (passen zur OKS der Zeile)
      'fehlt'  OKS_Tausch nicht in den Stempeldaten: Werte der Zeile gehören zu einem anderen
               Stempel und dürfen nicht benutzt werden
    """
    tausch = re.sub(r"\s+", "", zelle_text(zeile.get("OKS_Tausch", "")))
    oks = tausch or re.sub(r"\s+", "", zelle_text(zeile.get("OKS", "")))
    if oks in stempel_nach_oks:
        return stempel_nach_oks[oks], "csv"
    if tausch:
        return Stempel(oks, nummer="", name=""), "fehlt"
    return Stempel(oks, nummer=zelle_text(zeile.get("Stempel_Nummer", "")),
                   name=zelle_text(zeile.get("Stempel_Name", ""))), "liste"


# ---------------------------------------------------------------------------
# TEIL 2: Revit-Teil (dünn, defensiv). Wird nur in Dynamo ausgeführt.
# ---------------------------------------------------------------------------
def _eingaben_entpacken(eingaben):
    """Erlaubt, alle Eingaben als EINE Liste an IN[0] zu übergeben (ein Code-Block-Node)."""
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
    if wert is None or wert == "":
        return standard
    return wert


def _als_liste(wert):
    if wert is None:
        return []
    if isinstance(wert, (list, tuple)):
        return [w for w in wert if w not in (None, "")]
    return [wert]


def _eid(element_id):
    """ElementId -> int (Revit 2024+: .Value, davor .IntegerValue)."""
    wert = getattr(element_id, "Value", None)
    if wert is None:
        wert = element_id.IntegerValue
    return int(wert)


class Protokoll(object):
    def __init__(self):
        self.zeilen = []

    def __call__(self, text=""):
        self.zeilen.append(text)

    def kopf(self, titel):
        self.zeilen.append("")
        self.zeilen.append("=== %s ===" % titel)


def haupt(eingaben):
    """Einstieg aus dem Dynamo-Python-Node. Gibt das Protokoll als Liste zurück."""
    log = Protokoll()
    eingaben = _eingaben_entpacken(eingaben)
    try:
        _haupt(eingaben, log)
    except Exception as ex:  # nichts darf unkommentiert abbrechen
        import traceback
        log.kopf("FEHLER")
        log("Das Skript wurde abgebrochen: %s" % ex)
        log(traceback.format_exc())
    return log.zeilen


def _haupt(eingaben, log):
    import clr
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitServices")
    import Autodesk.Revit.DB as DB
    from RevitServices.Persistence import DocumentManager
    from RevitServices.Transactions import TransactionManager

    doc = DocumentManager.Instance.CurrentDBDocument
    app = doc.Application

    ordner = _eingabe(eingaben, 0, "")
    trockenlauf = bool(_eingabe(eingaben, 1, True))
    raeume_anlegen = bool(_eingabe(eingaben, 2, False))
    ebenen_zuordnung = parse_ebenen_zuordnung(_als_liste(_eingabe(eingaben, 3, STANDARD_EBENEN)))
    einheit = str(_eingabe(eingaben, 4, "m"))
    phase_name = str(_eingabe(eingaben, 5, "Bestand"))
    tol_sicher = float(_eingabe(eingaben, 6, 5))
    tol_max = float(_eingabe(eingaben, 7, 15))
    max_abstand_m = float(_eingabe(eingaben, 8, 10))
    praefix = str(_eingabe(eingaben, 9, "Raum-Nr. "))
    sp_datei = str(_eingabe(eingaben, 10, ""))
    liste_pfad = str(_eingabe(eingaben, 11, ""))
    liste_ordner = os.path.dirname(liste_pfad) if liste_pfad else ""
    ausgabe = str(_eingabe(eingaben, 12, "")) or liste_ordner or os.path.join(ordner, "_Ausgabe")
    manuelle_links = {}
    for z in _als_liste(_eingabe(eingaben, 13, [])):
        if "=" in str(z):
            k, v = str(z).split("=", 1)
            manuelle_links[norm_dwgname(k)] = norm_dwgname(v)
    parameter_anlegen = bool(_eingabe(eingaben, 14, True))
    ausgabeformat = str(_eingabe(eingaben, 16, "xlsx")).strip().lower()
    formate = {"xlsx": ("xlsx",), "excel": ("xlsx",), "csv": ("csv",),
               "beides": ("xlsx", "csv"), "both": ("xlsx", "csv")}.get(ausgabeformat, ("xlsx",))
    ausschluss_suffix = str(_eingabe(eingaben, 15, STANDARD_AUSSCHLUSS_SUFFIX))
    if ausschluss_suffix.strip().lower() in ("-", "keine"):
        ausschluss_suffix = ""

    zeit = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log.kopf("Raumstempel -> Revit-Räume  (%s)" % zeit)
    log("Modus: %s" % ("TROCKENLAUF - es wird nichts geschrieben" if trockenlauf
                       else "SCHREIBEN (Transaktion)"))
    log("Phase: %s | Einheit DWG: %s | Schwellen: sicher <= %.1f %%, Vorschlag <= %.1f %%"
        % (phase_name, einheit, tol_sicher, tol_max))
    einheit_in_fuss(einheit)  # prüft die Eingabe früh

    pruefliste = []     # dicts für Pruefliste_*.csv

    def pruef(kat, ebene="", oks="", nummer="", name="", raum_id="", detail=""):
        pruefliste.append({"Kategorie": kat, "Ebene": ebene, "OKS": oks, "Stempel_Nummer": nummer,
                           "Name": name, "Raum_ID": raum_id, "Detail": detail})

    # --- Parameter prüfen / anlegen ----------------------------------------
    params_ok = _stelle_parameter_sicher(doc, app, DB, TransactionManager, sp_datei, ausgabe,
                                         parameter_anlegen and not trockenlauf, log, pruef)

    # --- Lauf 2 mit geprüfter Liste ----------------------------------------
    if liste_pfad:
        _lauf_mit_liste(doc, DB, TransactionManager, liste_pfad, praefix, trockenlauf,
                        params_ok, ordner, ausschluss_suffix, phase_name, ebenen_zuordnung, log, pruef)
        _ausgabe_listen(ausgabe, zeit, None, pruefliste, log, formate)
        return

    # --- Stempel lesen -------------------------------------------------------
    if not ordner or not os.path.isdir(ordner):
        raise ValueError("Ordner mit CSV-Dateien nicht gefunden: '%s'" % ordner)
    ignoriert_codes = {}
    stempel, mel = lese_stempel_ordner(ordner, ausschluss_suffix, ignoriert_codes)
    for m in mel:
        log("Hinweis: " + m)
        pruef("Einlesen", detail=m)
    vorhandene_codes = {s.code for s in stempel}
    for code in sorted(c for c in ignoriert_codes if c not in vorhandene_codes):
        msg = ("Geschoss %s: nur Zeilen aus externen Referenzen (%d), keine eigenen Stempel - "
               "nicht verarbeitet" % (code, ignoriert_codes[code]))
        log("WARNUNG: " + msg)
        pruef("Ebene nicht verarbeitet", ebene=code or "", detail=msg)
    log("%d Stempel aus %s gelesen." % (len(stempel), ordner))

    # doppelte OKS: identische Kopien reduzieren, widersprüchliche nicht verarbeiten
    stempel_liste, identisch, konflikte = bereinige_doppelte(stempel)
    if identisch:
        quellen = sorted({x.quelle for x in identisch})
        log("Hinweis: %d identische Stempel mehrfach vorhanden (CSV-Dateien: %s) - je einmal verwendet. "
            "Tipp: Nicht benötigte CSV-Dateien aus dem Ordner nehmen."
            % (len(identisch), ", ".join(quellen)))
        pruef("Doppelte OKS (identisch)", detail="%d identische Kopien entfernt (%s)"
              % (len(identisch), ", ".join(quellen)))
    for oks, g in konflikte.items():
        for x in g:
            pruef("Doppelte OKS", oks=oks, name=x.name, nummer=x.nummer,
                  detail="OKS %d-mal mit unterschiedlichen Werten (%s Zeile %d) - nicht verarbeitet"
                         % (len(g), x.quelle, x.zeile))
    if konflikte:
        log("ACHTUNG: %d OKS kommen mit widersprüchlichen Werten mehrfach vor - nicht verarbeitet."
            % len(konflikte))

    # --- Revit-Räume (nur Phase) ---------------------------------------------
    raeume_info, phasen, raum_fehler = _sammle_raeume(doc, DB, phase_name)
    if raum_fehler:
        log("WARNUNG: %d Räume konnten nicht gelesen werden und fehlen in der Auswertung, z. B.: %s"
            % (len(raum_fehler), "; ".join(raum_fehler[:3])))
        pruef("Räume nicht lesbar", detail="%d Räume, z. B. %s" % (len(raum_fehler), "; ".join(raum_fehler[:3])))
    if phase_name.lower() not in [p.lower() for p in phasen]:
        raise ValueError("Phase '%s' nicht im Projekt. Vorhandene Phasen: %s"
                         % (phase_name, ", ".join(phasen)))
    alle_ebenen = [_elementname(l) for l in DB.FilteredElementCollector(doc).OfClass(DB.Level)]
    raeume_je_ebene = {}
    for r in raeume_info:
        if r["platziert"]:
            raeume_je_ebene[r["ebene"]] = raeume_je_ebene.get(r["ebene"], 0) + 1
    log("%d platzierte Räume der Phase '%s' in %d Ebenen." %
        (sum(raeume_je_ebene.values()), phase_name, len(raeume_je_ebene)))

    # --- Ebenen --------------------------------------------------------------
    codes_anzahl = {}
    for s in stempel_liste:
        codes_anzahl[s.code] = codes_anzahl.get(s.code, 0) + 1
    ebenen_bericht = pruefe_ebenen(codes_anzahl, ebenen_zuordnung, alle_ebenen, raeume_je_ebene)
    log.kopf("Ebenen")
    ebene_von_code = {}
    for e in ebenen_bericht:
        log("%s -> %s : %d Stempel, %s" % (e["code"], e["ebene"] or "-", e["anzahl"], e["status"]))
        if e["ok"]:
            ebene_von_code[e["code"]] = e["ebene"]
        else:
            pruef("Ebene nicht verarbeitet", ebene=e["code"] or "", detail="%d Stempel: %s"
                  % (e["anzahl"], e["status"]))

    # --- Zuordnung je Ebene ---------------------------------------------------
    zuordnungsliste = []
    gesamt_treffer = gesamt_stempel = 0
    jobs = []           # zu schreibende Zuordnungen
    anzahl_neu_kandidaten = []

    for code, ebene in sorted(ebene_von_code.items()):
        st_ebene = [s for s in stempel_liste if s.code == code]
        raeume_ebene = [r for r in raeume_info if r["ebene"] == ebene and r["platziert"]]
        log.kopf("Ebene %s (%s): %d Stempel, %d Räume" % (ebene, code, len(st_ebene), len(raeume_ebene)))

        trafo = _finde_trafo(doc, DB, st_ebene, ebene, manuelle_links, ausschluss_suffix, log, pruef)
        if trafo is None:
            for s in st_ebene:
                pruef("Stempel ohne Raum", ebene=ebene, oks=s.oks, nummer=s.nummer, name=s.name,
                      detail="Keine DWG-Verknüpfung für die Koordinaten gefunden")
            continue
        ebenenhoehe = raeume_ebene[0]["ebenenhoehe"]
        z_pruef = pruefpunkt_hoehe_fuss(ebenenhoehe)

        punkte = {}
        for i, s in enumerate(st_ebene):
            x, y, _z = dwg_nach_revit(s.punkt_x, s.punkt_y, einheit, trafo)
            punkte[i] = (x, y)
        _plausibilitaet(trafo, punkte, log, pruef, ebene)

        treffer = {}
        punkt_fehler = []
        for i, (x, y) in punkte.items():
            p = DB.XYZ(x, y, z_pruef)
            treffer[i] = []
            for r in raeume_ebene:
                try:
                    if r["element"].IsPointInRoom(p):
                        treffer[i].append(r["id"])
                except Exception as ex:
                    punkt_fehler.append("Raum %s: %s" % (r["id"], ex))
        if punkt_fehler:
            log("WARNUNG: IsPointInRoom schlug %d-mal fehl, z. B.: %s" % (len(punkt_fehler), punkt_fehler[0]))
            pruef("Punkt-in-Raum", ebene=ebene, detail="%d Fehler, z. B. %s" % (len(punkt_fehler), punkt_fehler[0]))
        n_treffer = len([i for i, t in treffer.items() if t])
        gesamt_treffer += n_treffer
        gesamt_stempel += len(st_ebene)
        log("Trefferquote Punkt-in-Raum: %d von %d Stempeln liegen in einem Raum." %
            (n_treffer, len(st_ebene)))
        if st_ebene and n_treffer < 0.6 * len(st_ebene):
            log("WARNUNG: niedrige Trefferquote - Versatz der Verknüpfung oder falsche Einheit prüfen!")

        raeume_dict = {r["id"]: {"flaeche": r["flaeche"], "pos": r["pos"]} for r in raeume_ebene}
        erg = ordne_zu(st_ebene, raeume_dict, treffer, positionen=punkte,
                       tol_sicher=tol_sicher, tol_max=tol_max,
                       max_abstand=max_abstand_m / METER_JE_FUSS)
        raum_nach_id = {r["id"]: r for r in raeume_ebene}
        for z in erg["zuordnungen"]:
            s, r = st_ebene[z.stempel_idx], raum_nach_id[z.raum_id]
            zuordnungsliste.append({
                "Ebene": ebene, "OKS": s.oks, "Stempel_Nummer": s.nummer,
                "Stempel_Name": s.name, "Stempel_Flaeche": s.flaeche,
                "Raum_ID": r["id"], "Raum_Nummer_alt": r["nummer"],
                "Raum_Name_alt": r["name"], "Raum_Flaeche": round(r["flaeche"], 2),
                "Abweichung_Prozent": None if z.abweichung is None else round(z.abweichung, 1),
                "Methode": z.methode, "Status": z.status,
                "Freigabe": "J" if z.status == "sicher" else "N", "Bemerkung": z.bemerkung})
            jobs.append((s, r, z, ebene))
            if z.abweichung is not None and z.abweichung >= 5.0:
                pruef("Flächenabweichung", ebene=ebene, oks=s.oks, nummer=s.nummer, name=s.name,
                      raum_id=r["id"], detail="Stempel %.2f m2, Revit %.2f m2 (%.1f %%) - nur Vergleich"
                      % (s.flaeche, r["flaeche"], z.abweichung))
            if z.status == "unsicher":
                pruef("Unsichere Zuordnung", ebene=ebene, oks=s.oks, nummer=s.nummer, name=s.name,
                      raum_id=r["id"], detail=z.bemerkung)
        for si in erg["stempel_ohne_raum"]:
            s = st_ebene[si]
            pruef("Stempel ohne Raum", ebene=ebene, oks=s.oks, nummer=s.nummer, name=s.name,
                  detail="Punkt in Raum: %s" % ("ja, Raum bereits anderem Stempel zugeordnet"
                                                if treffer.get(si) else "nein"))
            if not treffer.get(si):
                anzahl_neu_kandidaten.append((s, ebene, punkte[si]))
        for rid in erg["raeume_ohne_stempel"]:
            r = raum_nach_id[rid]
            pruef("Raum ohne Stempel", ebene=ebene, nummer=r["nummer"], name=r["name"], raum_id=rid,
                  detail="%.2f m2" % r["flaeche"])
        for rid, sis in erg["raeume_mehrfach"].items():
            r = raum_nach_id[rid]
            pruef("Raum mit mehreren Stempeln", ebene=ebene, nummer=r["nummer"], name=r["name"],
                  raum_id=rid, detail="%d Stempel: %s" % (len(sis), ", ".join(st_ebene[i].oks for i in sis)))
        log("Zugeordnet: %d (sicher: %d, unsicher: %d) | Stempel ohne Raum: %d | Räume ohne Stempel: %d"
            % (len(erg["zuordnungen"]), len([z for z in erg["zuordnungen"] if z.status == "sicher"]),
               len([z for z in erg["zuordnungen"] if z.status == "unsicher"]),
               len(erg["stempel_ohne_raum"]), len(erg["raeume_ohne_stempel"])))
        # Räume ohne Stempel auch anderer Ebenen werden oben je Ebene gemeldet.

    if gesamt_stempel:
        log.kopf("Gesamt-Trefferquote")
        log("%d von %d Stempeln wurden per Punkt-in-Raum in einem Raum gefunden." %
            (gesamt_treffer, gesamt_stempel))

    # --- Nummern prüfen --------------------------------------------------------
    log.kopf("Raumnummern (gekürzt)")
    nummern = {}
    for s, r, z, ebene in jobs:
        n, fehler = kurz_nummer(s.oks)
        if fehler:
            pruef("Nummer zu kurz/ungültig", ebene=ebene, oks=s.oks, raum_id=r["id"], detail=fehler)
            log("Hinweis: " + fehler)
        else:
            nummern[s.oks] = n
    # auch alle Stempel ohne Zuordnung auf Eindeutigkeit prüfen
    for s in stempel_liste:
        if s.oks not in nummern:
            n, fehler = kurz_nummer(s.oks)
            if not fehler:
                nummern[s.oks] = n
    doppelte_nr = finde_doppelte(nummern)
    gesperrte_nr = set()
    for nr, okslist in doppelte_nr.items():
        gesperrte_nr.add(nr)
        pruef("Doppelte Nummer", oks=", ".join(sorted(okslist)), nummer=nr,
              detail="Gekürzte Nummer nicht eindeutig - wird nicht geschrieben")
        log("Doppelte gekürzte Nummer %s: %s" % (nr, ", ".join(sorted(okslist))))
    ziel_raeume = {r["id"] for _, r, _, _ in jobs}
    fremd_nr = {r["nummer"]: r for r in raeume_info if r["id"] not in ziel_raeume and r["nummer"]}
    for nr in set(nummern.values()):
        if nr in fremd_nr:
            gesperrte_nr.add(nr)
            pruef("Doppelte Nummer", nummer=nr, raum_id=fremd_nr[nr]["id"],
                  detail="Nummer ist im Projekt bereits an einem anderen Raum vergeben")
            log("Nummer %s ist im Projekt schon an Raum %s vergeben." % (nr, fremd_nr[nr]["id"]))
    if not doppelte_nr and not (set(nummern.values()) & set(fremd_nr)):
        log("Alle gekürzten Nummern sind eindeutig.")

    # --- Schreiben ---------------------------------------------------------------
    freigegeben = [(s, r, z, e) for s, r, z, e in jobs if z.status == "sicher"]
    log.kopf("Schreiben")
    if trockenlauf:
        log("Trockenlauf: %d Zuordnungen wären freigegeben (Status 'sicher'), %d unsicher. "
            "Es wurde NICHTS geschrieben." % (len(freigegeben), len(jobs) - len(freigegeben)))
        log("Prüfe die Zuordnungsliste, setze Freigabe auf J/N und starte mit Trockenlauf = False "
            "und dem Pfad der Liste (Eingabe 11).")
    elif not params_ok:
        log("Parameter %s / %s fehlen - es wird nichts geschrieben." % (PARAM_OKS, PARAM_NUMMER_TEXT))
    else:
        auftraege = []
        for s, r, z, e in freigegeben:
            werte, _fehler = schreibvorgaben(s, praefix)
            if werte["nummer"] in gesperrte_nr:
                werte["nummer"] = None
            auftraege.append((r, werte, e, s))
        _schreibe(doc, DB, TransactionManager, auftraege, log, pruef)
        if raeume_anlegen:
            _lege_raeume_an(doc, DB, TransactionManager, anzahl_neu_kandidaten, praefix, log, pruef)
    if raeume_anlegen and trockenlauf:
        log("Raumanlage: %d Stempel ohne Raum wären Kandidaten (nur ohne Treffer, geschlossene "
            "Umgrenzung nötig)." % len(anzahl_neu_kandidaten))

    _ausgabe_listen(ausgabe, zeit, zuordnungsliste, pruefliste, log, formate)


# --- Revit-Hilfsfunktionen ---------------------------------------------------
def _elementname(el):
    """Name eines Revit-Elements.

    Auf manchen Elementtypen (z. B. CADLinkType) wirft `el.Name` in Dynamo/CPython
    "property cannot be read". Dann wird der Name über die Basisklasse bzw. über
    Parameter gelesen.
    """
    if el is None:
        return ""
    try:
        return el.Name
    except Exception:
        pass
    try:
        import Autodesk.Revit.DB as DB_
        return DB_.Element.Name.__get__(el)
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


def _sammle_raeume(doc, DB, phase_name):
    """Alle Räume der gewünschten Phase. Räume anderer Phasen werden ignoriert."""
    phasen = [_elementname(p) for p in doc.Phases]
    ergebnis, fehler = [], []
    sammler = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Rooms) \
        .WhereElementIsNotElementType()
    for r in sammler:
        try:
            p = r.get_Parameter(DB.BuiltInParameter.ROOM_PHASE)
            phase = _elementname(doc.GetElement(p.AsElementId())) if p and p.AsElementId() else None
            if phase is None or phase.lower() != phase_name.lower():
                continue
            platziert = r.Location is not None and r.Area > 0
            ebene = _elementname(r.Level) if r.Level is not None else None
            pos = None
            if r.Location is not None and hasattr(r.Location, "Point"):
                pos = (r.Location.Point.X, r.Location.Point.Y)
            ergebnis.append({
                "element": r, "id": _eid(r.Id), "platziert": platziert, "ebene": ebene,
                "ebenenhoehe": r.Level.Elevation if r.Level is not None else 0.0,
                "flaeche": r.Area * M2_JE_FT2, "pos": pos,
                "nummer": r.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER).AsString() or "",
                "name": r.get_Parameter(DB.BuiltInParameter.ROOM_NAME).AsString() or ""})
        except Exception as ex:
            fehler.append("Raum %s: %s" % (_eid(r.Id), ex))
    return ergebnis, phasen, fehler


def _sammle_verknuepfungen(doc, DB):
    """Alle CAD-Instanzen des Modells (verknüpft oder importiert) mit normalisierten Namen.

    Gibt (liste, diagnose) zurück. liste: [(name_norm, instanz, verknuepft, rohname)],
    verknüpfte zuerst. Nichts wird still verschluckt: Fehler landen in diagnose.
    """
    liste, fehler, n_inst, n_verkn = [], [], 0, 0
    instanzen = []
    try:
        instanzen = list(DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance))
    except Exception as ex:
        fehler.append("OfClass(ImportInstance): %s" % ex)
    weg = "Sammler"
    if not instanzen:
        # zweiter Suchweg: über die CAD-Verknüpfungstypen und ihre abhängigen Instanzen
        try:
            for t in DB.FilteredElementCollector(doc).OfClass(DB.CADLinkType):
                for eid in t.GetDependentElements(DB.ElementClassFilter(DB.ImportInstance)):
                    el = doc.GetElement(eid)
                    if el is not None and isinstance(el, DB.ImportInstance):
                        instanzen.append(el)
            weg = "Verknüpfungstypen"
        except Exception as ex:
            fehler.append("Suche über CADLinkType: %s" % ex)
    for inst in instanzen:
        n_inst += 1
        try:
            verknuepft = bool(inst.IsLinked)
        except Exception as ex:
            verknuepft, _ = False, fehler.append("IsLinked: %s" % ex)
        n_verkn += 1 if verknuepft else 0
        namen = []
        try:
            typ = doc.GetElement(inst.GetTypeId())
            if typ is not None:
                namen.append(_elementname(typ))
        except Exception as ex:
            fehler.append("Typname: %s" % ex)
        try:
            if inst.Category is not None:
                namen.append(inst.Category.Name)
        except Exception as ex:
            fehler.append("Kategoriename: %s" % ex)
        for roh in namen:
            n = norm_dwgname(roh)
            if n:
                liste.append((n, inst, verknuepft, roh))
    liste.sort(key=lambda e: not e[2])
    typen = []
    try:
        typen = [_elementname(t) for t in DB.FilteredElementCollector(doc).OfClass(DB.CADLinkType)]
    except Exception as ex:
        fehler.append("CADLinkType: %s" % ex)
    return liste, {"instanzen": n_inst, "verknuepft": n_verkn, "typen": typen, "fehler": fehler,
                   "weg": weg}


def _finde_trafo(doc, DB, stempel_ebene, ebene, manuelle_links, suffix, log, pruef):
    """Sucht die DWG-Verknüpfung und liefert ihre Transformation.

    Die Stempel kommen aus '<Geschoss>.dwg', in Revit ist '<Geschoss>_Bestand.dwg'
    verknüpft: gleicher Nullpunkt und gleiche Einheiten, daher wird deren
    Transformation benutzt (siehe kandidaten_linknamen).
    Die DWG-Dateien müssen IN REVIT verknüpft sein (Einfügen > CAD verknüpfen);
    Dateien im Projektordner allein genügen nicht.
    """
    dateinamen = [s.dateiname for s in stempel_ebene if s.dateiname]
    verknuepfungen, diag = _sammle_verknuepfungen(doc, DB)
    name, hinweis = waehle_verknuepfung(dateinamen, [n for n, _i, _v, _r in verknuepfungen],
                                        suffix, manuelle_links)
    gefunden = []
    seen = set()
    for nm, i_, _v, _r in verknuepfungen:
        if name is not None and nm == norm_dwgname(name) and _eid(i_.Id) not in seen:
            seen.add(_eid(i_.Id))
            gefunden.append(i_)
    if hinweis:
        log("Hinweis: " + hinweis)
    if not gefunden:
        eindeutig = {_eid(i_.Id): n for n, i_, _v, _r in verknuepfungen}
        if len(eindeutig) == 1:
            gefunden = [verknuepfungen[0][1]]
            log("Keine Namensübereinstimmung, nehme die einzige CAD-Instanz im Modell (%s)." % verknuepfungen[0][3])
        else:
            gesucht = ", ".join(sorted({norm_dwgname(d) for d in dateinamen})) or "?"
            log("FEHLER: Keine passende DWG-Verknüpfung zu %s gefunden (gesucht auch mit '%s')." % (gesucht, suffix))
            log("  CAD-Instanzen im Modell: %d (verknüpft: %d, importiert: %d), gefunden über: %s."
                % (diag["instanzen"], diag["verknuepft"], diag["instanzen"] - diag["verknuepft"], diag["weg"]))
            log("  Namen: %s" % (", ".join(sorted({r for _n, _i, _v, r in verknuepfungen})) or "keine"))
            log("  CAD-Verknüpfungstypen: %s" % (", ".join(sorted(diag["typen"])) or "keine"))
            for f in diag["fehler"][:5]:
                log("  Fehler beim Lesen: %s" % f)
            if diag["instanzen"] == 0 and diag["typen"]:
                log("  -> Es gibt CAD-Verknüpfungstypen, aber keine platzierte Instanz. Ist die Verknüpfung "
                    "geladen und in einer Ansicht/auf einer Ebene platziert (nicht nur 'ausgeblendet/entfernt')?")
            elif diag["instanzen"] == 0:
                log("  -> Das Modell enthält keine DWG. Verknüpfe sie in Revit (Einfügen > CAD verknüpfen). "
                    "DWG-Dateien im Projektordner allein genügen nicht.")
            else:
                log("  -> Namen passen nicht: trage die Zuordnung in Eingabe 13 ein "
                    "(['Dateiname.dwg=Name aus der Liste oben']).")
            return None
    inst = gefunden[0]
    if len(gefunden) > 1:
        try:
            auf_ebene = [i for i in gefunden if _elementname(doc.GetElement(i.LevelId)) == ebene]
            if auf_ebene:
                inst = auf_ebene[0]
        except Exception:
            pass
        log("Hinweis: %d Instanzen der Verknüpfung, verwende ElementId %s." % (len(gefunden), _eid(inst.Id)))
    t = inst.GetTotalTransform()
    log("DWG-Verknüpfung: %s (ElementId %s)" % (_elementname(doc.GetElement(inst.GetTypeId())), _eid(inst.Id)))
    trafo = {"origin": (t.Origin.X, t.Origin.Y, t.Origin.Z),
             "basis_x": (t.BasisX.X, t.BasisX.Y, t.BasisX.Z),
             "basis_y": (t.BasisY.X, t.BasisY.Y, t.BasisY.Z),
             "basis_z": (t.BasisZ.X, t.BasisZ.Y, t.BasisZ.Z)}
    try:
        bb = inst.get_BoundingBox(None)
        if bb is not None:
            trafo["bbox"] = (bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y)
    except Exception:
        pass
    return trafo


def _plausibilitaet(trafo, punkte, log, pruef, ebene):
    """Warnt, wenn die umgerechneten Punkte weitgehend außerhalb der Verknüpfung liegen."""
    bb = trafo.get("bbox")
    if not bb or not punkte:
        return
    rand = 1.0 / METER_JE_FUSS
    innen = len([1 for (x, y) in punkte.values()
                 if bb[0] - rand <= x <= bb[2] + rand and bb[1] - rand <= y <= bb[3] + rand])
    log("Plausibilität: %d von %d umgerechneten Punkten liegen im Umriss der Verknüpfung." % (innen, len(punkte)))
    if innen < 0.8 * len(punkte):
        msg = "Nur %d von %d Punkten im Umriss der DWG-Verknüpfung - Einheit/Verknüpfung prüfen." % (innen, len(punkte))
        log("WARNUNG: " + msg)
        pruef("Koordinaten", ebene=ebene, detail=msg)


def _setze(param, wert, DB):
    """Schreibt einen Textwert nur bei Änderung. Gibt 'geschrieben'|'unverändert'|Fehlertext."""
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


def _ist_beschreibbar(doc, DB, raum):
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


def _schreibe(doc, DB, TransactionManager, auftraege, log, pruef):
    """Alle Schreibvorgänge in EINER Transaktion; je Raum abgefangen."""
    TransactionManager.Instance.ForceCloseTransaction()
    stats = {"geschrieben": 0, "unverändert": 0, "fehler": 0, "gesperrt": 0}
    t = DB.Transaction(doc, "Raumstempel übertragen")
    t.Start()
    try:
        for raum_info, werte, ebene, s in auftraege:
            raum = raum_info["element"]
            ok, grund = _ist_beschreibbar(doc, DB, raum)
            if not ok:
                stats["gesperrt"] += 1
                pruef("Raum nicht beschreibbar", ebene=ebene, oks=s.oks, name=s.name,
                      raum_id=raum_info["id"], detail=grund)
                log("Raum %s nicht beschreibbar: %s" % (raum_info["id"], grund))
                continue
            ziele = [
                (raum.get_Parameter(DB.BuiltInParameter.ROOM_NAME), werte["name"], "Name"),
                (raum.LookupParameter(PARAM_OKS), werte["oks"], PARAM_OKS),
                (raum.LookupParameter(PARAM_NUMMER_TEXT), werte["nummer_text"], PARAM_NUMMER_TEXT),
                (raum.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER), werte["nummer"], "Nummer"),
            ]
            for param, wert, bez in ziele:
                if wert is None or (wert == "" and bez == PARAM_NUMMER_TEXT):
                    continue
                try:
                    erg = _setze(param, wert, DB)
                except Exception as ex:
                    erg = "Fehler: %s" % ex
                if erg in ("geschrieben", "unverändert"):
                    stats[erg] += 1
                else:
                    stats["fehler"] += 1
                    pruef("Schreibfehler", ebene=ebene, oks=s.oks, raum_id=raum_info["id"],
                          detail="%s: %s" % (bez, erg))
                    log("Raum %s, %s: %s" % (raum_info["id"], bez, erg))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    log("Geschrieben: %(geschrieben)d Werte, unverändert: %(unverändert)d, "
        "Fehler: %(fehler)d, gesperrte Räume: %(gesperrt)d" % stats)


def _lege_raeume_an(doc, DB, TransactionManager, kandidaten, praefix, log, pruef):
    """Optional: legt für Stempel ohne Treffer einen Raum an (nur bei geschlossener Umgrenzung)."""
    if not kandidaten:
        return
    TransactionManager.Instance.ForceCloseTransaction()
    t = DB.Transaction(doc, "Räume anlegen")
    t.Start()
    angelegt = 0
    try:
        for s, ebene, (x, y) in kandidaten:
            try:
                level = [l for l in DB.FilteredElementCollector(doc).OfClass(DB.Level)
                         if _elementname(l) == ebene][0]
                raum = doc.Create.NewRoom(level, DB.UV(x, y))
                if raum is None or raum.Area <= 0:
                    pruef("Raum nicht angelegt", ebene=ebene, oks=s.oks, name=s.name,
                          detail="Umgrenzung nicht geschlossen")
                    continue
                werte, _ = schreibvorgaben(s, praefix)
                raum.get_Parameter(DB.BuiltInParameter.ROOM_NAME).Set(werte["name"])
                for pname, wert in ((PARAM_OKS, werte["oks"]), (PARAM_NUMMER_TEXT, werte["nummer_text"])):
                    p = raum.LookupParameter(pname)
                    if p is not None and not p.IsReadOnly:
                        p.Set(wert)
                if werte["nummer"]:
                    raum.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER).Set(werte["nummer"])
                angelegt += 1
                pruef("Raum neu angelegt", ebene=ebene, oks=s.oks, name=s.name,
                      raum_id=_eid(raum.Id), detail="Bitte prüfen")
            except Exception as ex:
                pruef("Raum nicht angelegt", ebene=ebene, oks=s.oks, name=s.name, detail=str(ex))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    log("Neu angelegte Räume: %d" % angelegt)


def _param_text(el, name):
    """Textwert eines Parameters oder '' (auch wenn der Parameter nicht existiert)."""
    try:
        p = el.LookupParameter(name)
        return (p.AsString() or "") if p is not None else ""
    except Exception:
        return ""


def _lauf_mit_liste(doc, DB, TransactionManager, liste_pfad, praefix, trockenlauf,
                    params_ok, ordner, ausschluss_suffix, phase_name, ebenen_zuordnung, log, pruef):
    """Lauf 2: schreibt die geprüfte Zuordnungsliste (nur Zeilen mit Freigabe).

    Aus der Liste kommen nur OKS -> Raum_ID und Freigabe. Name und Nummer der Stempel
    werden aus den Original-CSV-Dateien (Eingabe 0) gelesen.
    """
    stempel_nach_oks = {}
    if ordner and os.path.isdir(ordner):
        st, _mel_csv = lese_stempel_ordner(ordner, ausschluss_suffix)
        eindeutig, _identisch, konflikte = bereinige_doppelte(st)
        stempel_nach_oks = {x.oks: x for x in eindeutig}
        log("Stempel aus %s gelesen: %d (Name und Nummer kommen aus den CSV-Dateien)." % (ordner, len(st)))
        for oks in konflikte:
            log("Hinweis: OKS %s mit widersprüchlichen Werten in den CSV-Dateien - Rückfall auf die Liste." % oks)
    else:
        log("Hinweis: CSV-Ordner nicht gefunden - Name und Nummer werden aus der Liste gelesen.")
    zeilen, mel = lese_zuordnungsliste(liste_pfad)
    for m in mel:
        log("Hinweis: " + m)
        pruef("Zuordnungsliste", detail=m)
    freigegeben = [z for z in zeilen if z["_freigabe"]]
    log("Zuordnungsliste: %d Zeilen, %d freigegeben." % (len(zeilen), len(freigegeben)))
    mit_tausch = [z for z in zeilen if zelle_text(z.get("OKS_Tausch", ""))]
    if mit_tausch:
        log("%d Zeilen mit OKS_Tausch (gilt statt der Spalte OKS), davon %d freigegeben."
            % (len(mit_tausch), len([z for z in mit_tausch if z["_freigabe"]])))
        for z in mit_tausch:
            if not z["_freigabe"]:
                log("Hinweis: Raum %s hat OKS_Tausch '%s', aber Freigabe ist nicht J - nicht geschrieben."
                    % (z["Raum_ID"], zelle_text(z.get("OKS_Tausch", ""))))
    nummern, auftraege = {}, []
    for z in freigegeben:
        raum = doc.GetElement(DB.ElementId(z["Raum_ID"]))
        if raum is None or raum.Category is None or \
                raum.Category.Id != DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_Rooms).Id:
            pruef("Raum nicht gefunden", oks=z.get("OKS", ""), raum_id=z["Raum_ID"],
                  detail="Element existiert nicht oder ist kein Raum")
            continue
        s, quelle = stempel_fuer_zeile(z, stempel_nach_oks)
        if quelle == "fehlt" or (quelle == "liste" and stempel_nach_oks):
            # OKS bzw. OKS_Tausch steht nicht in den Stempeldaten: nicht mit den alten
            # Werten der Zeile (Name/Nummer des früheren Stempels) vermischen
            pruef("OKS nicht in Stempeldaten", oks=s.oks, raum_id=z["Raum_ID"],
                  detail="Diese OKS steht in keiner Stempeldatei (Tippfehler?) - nicht geschrieben")
            log("OKS '%s' (Raum %s) steht in keiner Stempeldatei - nicht geschrieben." % (s.oks, z["Raum_ID"]))
            continue
        if quelle == "liste":
            log("Hinweis: OKS %s nicht in den CSV-Dateien, nehme Werte aus der Liste." % s.oks)
        raum_ebene = _elementname(raum.Level) if getattr(raum, "Level", None) is not None else ""
        if geschoss_passt(s.oks, raum_ebene, ebenen_zuordnung) is False:
            pruef("Geschoss passt nicht", ebene=raum_ebene, oks=s.oks, raum_id=z["Raum_ID"],
                  detail="Stempel gehört zum Geschoss %s, der Raum liegt auf '%s' - nicht geschrieben"
                         % (geschosscode(s.oks), raum_ebene))
            log("OKS %s passt nicht zur Ebene '%s' von Raum %s - nicht geschrieben."
                % (s.oks, raum_ebene, z["Raum_ID"]))
            continue
        werte, fehler = schreibvorgaben(s, praefix)
        if fehler:
            pruef("Nummer zu kurz/ungültig", oks=s.oks, raum_id=z["Raum_ID"], detail=fehler)
        auftraege.append(({"element": raum, "id": z["Raum_ID"]}, werte, z.get("Ebene", ""), s))
        if werte["nummer"]:
            nummern[s.oks] = werte["nummer"]
    doppelt = finde_doppelte(nummern)
    for nr, okslist in doppelt.items():
        pruef("Doppelte Nummer", oks=", ".join(okslist), nummer=nr, detail="nicht eindeutig - nicht geschrieben")
        log("Doppelte gekürzte Nummer %s: %s" % (nr, ", ".join(okslist)))
    for _r, werte, _e, _s in auftraege:
        if werte["nummer"] in doppelt:
            werte["nummer"] = None

    # Konflikte mit dem Ist-Zustand der Räume (z. B. Liste nach einem früheren Lauf 2 geändert)
    raeume_info, _phasen, _fehler = _sammle_raeume(doc, DB, phase_name)
    raeume_ist = {r["id"]: {"oks": _param_text(r["element"], PARAM_OKS), "nummer": r["nummer"]}
                  for r in raeume_info}
    jobs = [{"raum_id": r["id"], "oks": s.oks, "nummer": werte["nummer"]}
            for r, werte, _e, s in auftraege]
    konflikte = pruefe_schreibkonflikte(jobs, raeume_ist)
    ueberspringen = set()
    for rid, idx in konflikte["mehrfach"].items():
        ueberspringen.update(idx)
        oks_liste = ", ".join(jobs[i]["oks"] for i in idx)
        pruef("Raum mehrfach in der Liste", oks=oks_liste, raum_id=rid,
              detail="Mehrere freigegebene Stempel für denselben Raum - keiner geschrieben")
        log("Raum %s steht mehrfach in der Liste (%s) - nicht geschrieben." % (rid, oks_liste))
    for i, andere in konflikte["oks"].items():
        ueberspringen.add(i)
        pruef("OKS bereits an anderem Raum", oks=jobs[i]["oks"], raum_id=jobs[i]["raum_id"],
              detail="OKS steht danach auch an Raum %s - nicht geschrieben (Wert dort zuerst löschen "
                     "oder diesen Raum ändern)" % ", ".join(str(a) for a in andere))
        log("OKS %s steht schon an Raum %s - Zeile für Raum %s nicht geschrieben."
            % (jobs[i]["oks"], ", ".join(str(a) for a in andere), jobs[i]["raum_id"]))
    for i, andere in konflikte["nummer"].items():
        if i in ueberspringen:
            continue
        auftraege[i][1]["nummer"] = None
        pruef("Doppelte Nummer", oks=jobs[i]["oks"], nummer=jobs[i]["nummer"], raum_id=jobs[i]["raum_id"],
              detail="Nummer ist schon an Raum %s vergeben - Nummer nicht geschrieben"
                     % ", ".join(str(a) for a in andere))
    auftraege = [a for i, a in enumerate(auftraege) if i not in ueberspringen]

    if trockenlauf:
        log("Trockenlauf: %d Räume würden beschrieben. Nichts geschrieben." % len(auftraege))
    elif not params_ok:
        log("Parameter %s / %s fehlen - nichts geschrieben." % (PARAM_OKS, PARAM_NUMMER_TEXT))
    else:
        _schreibe(doc, DB, TransactionManager, auftraege, log, pruef)


def _schreibe_liste_geprueft(ausgabe, name, schreiber, log):
    """Schreibt eine Liste, prüft danach, ob sie wirklich existiert, und weicht bei Fehlern aus.

    schreiber: Funktion pfad -> None, die die Datei schreibt.
    Reihenfolge der Ordner: Ausgabeordner, Benutzer-Dokumente, Temp-Ordner.
    Gibt den tatsächlichen Pfad zurück (oder None).
    """
    import tempfile
    kandidaten = [ausgabe, os.path.join(os.path.expanduser("~"), "Documents"), tempfile.gettempdir()]
    for ordner in kandidaten:
        try:
            pfad = os.path.normpath(os.path.join(ordner, name))
            schreiber(pfad)
            if os.path.isfile(pfad) and os.path.getsize(pfad) > 0:
                if ordner != ausgabe:
                    log("WARNUNG: Ausgabeordner nicht beschreibbar, Datei liegt stattdessen hier:")
                log("%s  (%d Bytes)" % (pfad, os.path.getsize(pfad)))
                return pfad
            log("WARNUNG: %s wurde geschrieben, ist danach aber nicht auffindbar." % pfad)
        except Exception as ex:
            log("WARNUNG: Schreiben nach '%s' fehlgeschlagen: %s" % (ordner, ex))
    return None


def _ausgabe_listen(ausgabe, zeit, zuordnungsliste, pruefliste, log, formate=("xlsx",)):
    log.kopf("Listen")
    ausgabe = os.path.normpath(ausgabe)
    log("Ausgabeordner: %s" % ausgabe)

    def liste(titel, name, blatt, spalten, zeilen, textspalten, zahlenformat, freigabe, markiere):
        log("%s:" % titel)
        if "xlsx" in formate:
            _schreibe_liste_geprueft(
                ausgabe, name + ".xlsx",
                lambda pfad: schreibe_xlsx(pfad, blatt, spalten, zeilen, zahlenformat, freigabe, markiere), log)
        if "csv" in formate:
            _schreibe_liste_geprueft(
                ausgabe, name + ".csv",
                lambda pfad: schreibe_csv(pfad, spalten, csv_zeilen(zeilen, textspalten, zahlenformat)), log)

    if zuordnungsliste is not None:
        liste("Zuordnungsliste", "Zuordnungsliste_%s" % zeit, "Zuordnung", ZUORDNUNG_SPALTEN,
              zuordnungsliste, ZUORDNUNG_TEXTSPALTEN, ZUORDNUNG_ZAHLENFORMAT, "Freigabe",
              lambda z: z.get("Status") == "unsicher")
    liste("Prüfliste", "Pruefliste_%s" % zeit, "Prüfliste", PRUEF_SPALTEN, pruefliste,
          PRUEF_TEXTSPALTEN, {}, None, None)
    log.kopf("Prüfliste (Zusammenfassung)")
    zaehler = {}
    for z in pruefliste:
        zaehler[z["Kategorie"]] = zaehler.get(z["Kategorie"], 0) + 1
    if not zaehler:
        log("Keine Auffälligkeiten.")
    for kat in sorted(zaehler):
        log("%-30s %d" % (kat, zaehler[kat]))
        for z in [p for p in pruefliste if p["Kategorie"] == kat][:15]:
            log("   - %s %s %s %s %s" % (z["Ebene"], z["OKS"], z["Name"],
                                         ("ID " + str(z["Raum_ID"])) if z["Raum_ID"] != "" else "", z["Detail"]))


def _stelle_parameter_sicher(doc, app, DB, TransactionManager, sp_datei, ausgabe,
                             anlegen, log, pruef):
    """Prüft RaumOKS/Raumnummer_Text an Räumen; bindet sie bei Bedarf.

    Bevorzugt aus der Firmen-Shared-Parameter-Datei (gleiche GUID wie in der
    Firmenvorlage). Nur wenn dort nicht vorhanden/keine Datei: eigene Datei,
    mit deutlichem Hinweis.
    """
    log.kopf("Parameter")
    raum_kat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Rooms)

    def gebunden(name):
        it = doc.ParameterBindings.ForwardIterator()
        while it.MoveNext():
            if it.Key.Name == name:
                b = it.Current
                try:
                    return b.Categories.Contains(raum_kat)
                except Exception:
                    return False
        return False

    fehlend = [n for n in (PARAM_OKS, PARAM_NUMMER_TEXT) if not gebunden(n)]
    if not fehlend:
        log("Parameter %s und %s sind an Räume gebunden." % (PARAM_OKS, PARAM_NUMMER_TEXT))
        return True
    log("Fehlende Parameter an Räumen: %s" % ", ".join(fehlend))
    if not anlegen:
        log("Nicht angelegt (Trockenlauf oder Eingabe 14 = False). Im Schreiblauf werden sie angelegt.")
        pruef("Parameter fehlen", detail=", ".join(fehlend))
        return False

    alter_pfad = app.SharedParametersFilename
    eigene_datei = False
    try:
        TransactionManager.Instance.ForceCloseTransaction()
        tg = DB.TransactionGroup(doc, "Raumstempel-Parameter")
        tg.Start()
        t = DB.Transaction(doc, "Parameter binden")
        t.Start()
        try:
            def definition_suchen(dateipfad):
                if dateipfad:
                    app.SharedParametersFilename = dateipfad
                datei = app.OpenSharedParameterFile()
                return datei

            firmen = definition_suchen(sp_datei or alter_pfad) if (sp_datei or alter_pfad) else None
            for name in fehlend:
                definition = None
                if firmen is not None:
                    for g in firmen.Groups:
                        for d in g.Definitions:
                            if d.Name == name:
                                definition = d
                if definition is None:
                    # eigene Datei nur als Notlösung
                    eigene_datei = True
                    eigener_pfad = os.path.join(ausgabe, "Raumstempel_SharedParameters.txt")
                    if not os.path.isfile(eigener_pfad):
                        if not os.path.isdir(ausgabe):
                            os.makedirs(ausgabe)
                        with io.open(eigener_pfad, "w", encoding="utf-16") as f:
                            f.write("# This is a Revit shared parameter file.\n"
                                    "# Do not edit manually.\n"
                                    "*META\tVERSION\tMINVERSION\nMETA\t2\t1\n"
                                    "*GROUP\tID\tNAME\n"
                                    "*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\t"
                                    "DESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\n")
                    eigene = definition_suchen(eigener_pfad)
                    gruppe = None
                    for g in eigene.Groups:
                        if g.Name == PARAM_GRUPPE:
                            gruppe = g
                    if gruppe is None:
                        gruppe = eigene.Groups.Create(PARAM_GRUPPE)
                    for d in gruppe.Definitions:
                        if d.Name == name:
                            definition = d
                    if definition is None:
                        opt = DB.ExternalDefinitionCreationOptions(name, DB.SpecTypeId.String.Text)
                        definition = gruppe.Definitions.Create(opt)
                kategorien = app.Create.NewCategorySet()
                kategorien.Insert(raum_kat)
                bindung = app.Create.NewInstanceBinding(kategorien)
                try:
                    ok = doc.ParameterBindings.Insert(definition, bindung, DB.GroupTypeId.Data)
                except Exception:
                    ok = doc.ParameterBindings.ReInsert(definition, bindung, DB.GroupTypeId.Data)
                log("Parameter '%s' %s." % (name, "gebunden" if ok else "konnte nicht gebunden werden"))
            t.Commit()
            tg.Assimilate()
        except Exception:
            t.RollBack()
            tg.RollBack()
            raise
    finally:
        try:
            app.SharedParametersFilename = alter_pfad
        except Exception:
            pass
    if eigene_datei:
        log("WICHTIG: Die Parameter wurden NICHT aus der Firmendatei geladen, sondern in einer eigenen "
            "Shared-Parameter-Datei neu angelegt (%s). Shared Parameter werden über die GUID "
            "identifiziert. Kommen RaumOKS/Raumnummer_Text später in die Firmenvorlage, entstehen "
            "zwei verschiedene Parameter gleichen Namens." % os.path.join(ausgabe, "Raumstempel_SharedParameters.txt"))
        pruef("Parameter", detail="Parameter in eigener Shared-Parameter-Datei neu angelegt (neue GUID)")
    return all(gebunden(n) for n in (PARAM_OKS, PARAM_NUMMER_TEXT))


# ---------------------------------------------------------------------------
# Dynamo-Einstieg: IN/OUT stellt Dynamo bereit. Beim Import (Tests) passiert nichts.
# ---------------------------------------------------------------------------
if "IN" in globals():
    OUT = haupt(IN)  # noqa: F821
