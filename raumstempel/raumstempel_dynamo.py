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

Alternativ können alle 16 Werte als EINE Liste an IN[0] übergeben werden
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
    """Parst CSV-Text. Gibt (stempel_liste, meldungen) zurück.

    Zeilen aus externen Referenzen (Dateiname endet auf `ausschluss_suffix`)
    werden übersprungen und in einer Sammelmeldung gezählt. `ignoriert_out`
    (dict, optional) sammelt je Geschosscode die Zahl der ignorierten Zeilen.
    """
    meldungen = []
    stempel = []
    ignoriert = {}
    text = text.lstrip("﻿")
    zeilen = text.splitlines()
    if not zeilen:
        return [], ["%s: Datei ist leer." % quelle]
    trenner = _erkenne_trenner(zeilen[0])
    leser = csv.reader(io.StringIO(text), delimiter=trenner)
    kopf = next(leser, None)
    if not kopf:
        return [], ["%s: keine Kopfzeile." % quelle]

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

    for nr, zeile in enumerate(leser, start=2):
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


def lese_stempel_ordner(ordner, ausschluss_suffix=STANDARD_AUSSCHLUSS_SUFFIX, ignoriert_out=None):
    """Liest alle *.csv im Ordner (nicht rekursiv). Gibt (stempel, meldungen)."""
    dateien = sorted(glob.glob(os.path.join(ordner, "*.csv")))
    alle, meldungen = [], []
    if not dateien:
        meldungen.append("Keine CSV-Dateien im Ordner gefunden: %s" % ordner)
    for pfad in dateien:
        name = os.path.basename(pfad)
        with open(pfad, "rb") as f:
            text = dekodiere(f.read())
        st, mel = parse_stempel_text(text, quelle=name, ausschluss_suffix=ausschluss_suffix,
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
ZUORDNUNG_SPALTEN = ["Ebene", "OKS", "Stempel_Nummer", "Stempel_Name", "Stempel_Flaeche",
                     "Raum_ID", "Raum_Nummer_alt", "Raum_Name_alt", "Raum_Flaeche",
                     "Abweichung_Prozent", "Methode", "Status", "Freigabe", "Bemerkung"]
PRUEF_SPALTEN = ["Kategorie", "Ebene", "OKS", "Stempel_Nummer", "Name", "Raum_ID", "Detail"]


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


def ist_freigabe(wert):
    return str(wert or "").strip().lower() in ("j", "ja", "x", "1", "true", "y", "yes")


def lese_zuordnungsliste(pfad):
    """Liest die (geprüfte) Zuordnungsliste. Gibt (zeilen, meldungen)."""
    with open(pfad, "rb") as f:
        text = dekodiere(f.read()).lstrip("﻿")
    zeilen_roh = text.splitlines()
    if not zeilen_roh:
        return [], ["Zuordnungsliste ist leer."]
    leser = csv.DictReader(io.StringIO(text), delimiter=_erkenne_trenner(zeilen_roh[0]))
    ergebnis, meldungen = [], []
    for nr, z in enumerate(leser, start=2):
        try:
            rid = int(str(z.get("Raum_ID", "")).strip())
        except ValueError:
            if ist_freigabe(z.get("Freigabe")):
                meldungen.append("Zuordnungsliste Zeile %d (%s): freigegeben, aber Raum_ID fehlt."
                                 % (nr, z.get("OKS")))
            continue
        z["Raum_ID"] = rid
        z["_freigabe"] = ist_freigabe(z.get("Freigabe"))
        ergebnis.append(z)
    return ergebnis, meldungen


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
                        params_ok, log, pruef)
        _ausgabe_listen(ausgabe, zeit, None, pruefliste, log)
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

    # doppelte OKS: nicht verarbeiten, melden
    doppelt = finde_doppelte({i: s.oks for i, s in enumerate(stempel)})
    ausschluss = set()
    for oks, idx in doppelt.items():
        for i in idx:
            ausschluss.add(i)
            pruef("Doppelte OKS", oks=oks, name=stempel[i].name, nummer=stempel[i].nummer,
                  detail="OKS kommt %d-mal vor (%s Zeile %d) - nicht verarbeitet"
                         % (len(idx), stempel[i].quelle, stempel[i].zeile))
    stempel_liste = [s for i, s in enumerate(stempel) if i not in ausschluss]
    if ausschluss:
        log("ACHTUNG: %d Stempel mit doppelter OKS ausgeschlossen." % len(ausschluss))

    # --- Revit-Räume (nur Phase) ---------------------------------------------
    raeume_info, phasen = _sammle_raeume(doc, DB, phase_name)
    if phase_name.lower() not in [p.lower() for p in phasen]:
        raise ValueError("Phase '%s' nicht im Projekt. Vorhandene Phasen: %s"
                         % (phase_name, ", ".join(phasen)))
    alle_ebenen = [l.Name for l in DB.FilteredElementCollector(doc).OfClass(DB.Level)]
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
        for i, (x, y) in punkte.items():
            p = DB.XYZ(x, y, z_pruef)
            treffer[i] = []
            for r in raeume_ebene:
                try:
                    if r["element"].IsPointInRoom(p):
                        treffer[i].append(r["id"])
                except Exception:
                    continue
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
                "Ebene": ebene, "OKS": s.oks, "Stempel_Nummer": s.nummer, "Stempel_Name": s.name,
                "Stempel_Flaeche": s.flaeche, "Raum_ID": r["id"], "Raum_Nummer_alt": r["nummer"],
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

    _ausgabe_listen(ausgabe, zeit, zuordnungsliste, pruefliste, log)


# --- Revit-Hilfsfunktionen ---------------------------------------------------
def _sammle_raeume(doc, DB, phase_name):
    """Alle Räume der gewünschten Phase. Räume anderer Phasen werden ignoriert."""
    phasen = [p.Name for p in doc.Phases]
    ergebnis = []
    sammler = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Rooms) \
        .WhereElementIsNotElementType()
    for r in sammler:
        try:
            p = r.get_Parameter(DB.BuiltInParameter.ROOM_PHASE)
            phase = doc.GetElement(p.AsElementId()).Name if p and p.AsElementId() else None
            if phase is None or phase.lower() != phase_name.lower():
                continue
            platziert = r.Location is not None and r.Area > 0
            ebene = r.Level.Name if r.Level is not None else None
            pos = None
            if r.Location is not None and hasattr(r.Location, "Point"):
                pos = (r.Location.Point.X, r.Location.Point.Y)
            ergebnis.append({
                "element": r, "id": _eid(r.Id), "platziert": platziert, "ebene": ebene,
                "ebenenhoehe": r.Level.Elevation if r.Level is not None else 0.0,
                "flaeche": r.Area * M2_JE_FT2, "pos": pos,
                "nummer": r.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER).AsString() or "",
                "name": r.get_Parameter(DB.BuiltInParameter.ROOM_NAME).AsString() or ""})
        except Exception:
            continue
    return ergebnis, phasen


def _finde_trafo(doc, DB, stempel_ebene, ebene, manuelle_links, suffix, log, pruef):
    """Sucht die DWG-Verknüpfung und liefert ihre Transformation.

    Die Stempel kommen aus '<Geschoss>.dwg', in Revit ist '<Geschoss>_Bestand.dwg'
    verknüpft: gleicher Nullpunkt und gleiche Einheiten, daher wird deren
    Transformation benutzt (siehe kandidaten_linknamen).
    """
    dateinamen = [s.dateiname for s in stempel_ebene if s.dateiname]
    instanzen = list(DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance))
    verknuepfungen = []
    for inst in instanzen:
        try:
            if not inst.IsLinked:
                continue
            typ = doc.GetElement(inst.GetTypeId())
            verknuepfungen.append((norm_dwgname(typ.Name), inst))
        except Exception:
            continue
    name, hinweis = waehle_verknuepfung(dateinamen, [n for n, _ in verknuepfungen], suffix, manuelle_links)
    gefunden = [i for (nm, i) in verknuepfungen if name is not None and nm == name]
    if hinweis:
        log("Hinweis: " + hinweis)
    if not gefunden:
        if len(verknuepfungen) == 1:
            gefunden = [verknuepfungen[0][1]]
            log("Keine Namensübereinstimmung, nehme die einzige DWG-Verknüpfung (%s)." % verknuepfungen[0][0])
        else:
            log("FEHLER: Keine DWG-Verknüpfung zu %s gefunden (gesucht auch mit '%s'). Vorhandene: %s. "
                "Zuordnung per Eingabe 13 ('Dateiname=Verknüpfungsname')."
                % (", ".join(sorted({norm_dwgname(d) for d in dateinamen})) or "?", suffix,
                   ", ".join(sorted(n for n, _ in verknuepfungen)) or "keine"))
            return None
    inst = gefunden[0]
    if len(gefunden) > 1:
        try:
            auf_ebene = [i for i in gefunden if doc.GetElement(i.LevelId).Name == ebene]
            if auf_ebene:
                inst = auf_ebene[0]
        except Exception:
            pass
        log("Hinweis: %d Instanzen der Verknüpfung, verwende ElementId %s." % (len(gefunden), _eid(inst.Id)))
    t = inst.GetTotalTransform()
    log("DWG-Verknüpfung: %s (ElementId %s)" % (doc.GetElement(inst.GetTypeId()).Name, _eid(inst.Id)))
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
                         if l.Name == ebene][0]
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


def _lauf_mit_liste(doc, DB, TransactionManager, liste_pfad, praefix, trockenlauf,
                    params_ok, log, pruef):
    """Lauf 2: schreibt die geprüfte Zuordnungsliste (nur Zeilen mit Freigabe)."""
    zeilen, mel = lese_zuordnungsliste(liste_pfad)
    for m in mel:
        log("Hinweis: " + m)
        pruef("Zuordnungsliste", detail=m)
    freigegeben = [z for z in zeilen if z["_freigabe"]]
    log("Zuordnungsliste: %d Zeilen, %d freigegeben." % (len(zeilen), len(freigegeben)))
    nummern, auftraege = {}, []
    for z in freigegeben:
        raum = doc.GetElement(DB.ElementId(z["Raum_ID"]))
        if raum is None or raum.Category is None or \
                raum.Category.Id != DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_Rooms).Id:
            pruef("Raum nicht gefunden", oks=z.get("OKS", ""), raum_id=z["Raum_ID"],
                  detail="Element existiert nicht oder ist kein Raum")
            continue
        s = Stempel(z.get("OKS", ""), nummer=z.get("Stempel_Nummer", ""), name=z.get("Stempel_Name", ""))
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
    if trockenlauf:
        log("Trockenlauf: %d Räume würden beschrieben. Nichts geschrieben." % len(auftraege))
    elif not params_ok:
        log("Parameter %s / %s fehlen - nichts geschrieben." % (PARAM_OKS, PARAM_NUMMER_TEXT))
    else:
        _schreibe(doc, DB, TransactionManager, auftraege, log, pruef)


def _ausgabe_listen(ausgabe, zeit, zuordnungsliste, pruefliste, log):
    log.kopf("Listen")
    try:
        if zuordnungsliste is not None:
            pfad = os.path.join(ausgabe, "Zuordnungsliste_%s.csv" % zeit)
            schreibe_csv(pfad, ZUORDNUNG_SPALTEN, zuordnungsliste)
            log("Zuordnungsliste: %s" % pfad)
        pfad = os.path.join(ausgabe, "Pruefliste_%s.csv" % zeit)
        schreibe_csv(pfad, PRUEF_SPALTEN, pruefliste)
        log("Prüfliste: %s" % pfad)
    except Exception as ex:
        log("Listen konnten nicht geschrieben werden: %s" % ex)
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
