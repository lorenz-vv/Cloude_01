# -*- coding: utf-8 -*-
"""Ablauftests: das komplette Skript (haupt) gegen ein nachgebildetes Revit-Modell.

Fängt Fehler im Revit-Teil ab (falsche Namen, Signaturen, Reihenfolge), die die Tests der reinen
Logik nicht sehen. Echte Eigenheiten von Revit/pythonnet bildet fake_revit.py nur teilweise nach.
Start: python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "raumstempel"))
sys.path.insert(0, HIER)
import raumstempel_dynamo as rs  # noqa: E402
from fake_revit import FakeModell, M_JE_FUSS  # noqa: E402

EBENEN = ["G00=EG- OK FFB", "G01=1. OG - OK FFB"]
VERSATZ_M = 1.0          # Verschiebung der DWG-Verknüpfung in x (Meter)


def eingaben(ordner, ausgabe, trockenlauf=True, liste="", suffix="_Bestand", formate="xlsx"):
    """Der Code-Block der Anleitung (Positionen 0 bis 16)."""
    return [ordner, trockenlauf, False, EBENEN, "m", "Bestand", 5, 15, 10, "Raum-Nr. ", "", liste,
            ausgabe, [], True, suffix, formate]


def stempel_csv(pfad, zeilen):
    kopf = "Anzahl,Name,Dateiname,FM.FLAECHE,FM.NAME,FM.NUMMER,FM.OKS,Position X,Position Y,Position Z"
    with open(pfad, "w", encoding="utf-8-sig", newline="") as f:
        f.write(kopf + "\n")
        for z in zeilen:
            f.write(",".join(str(x) for x in ("1", "PIT_DOI_GMSH") + z) + ",0.0000\n")


class AblaufBasis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ordner = os.path.join(self.tmp.name, "csv")
        self.ausgabe = os.path.join(self.tmp.name, "ausgabe")
        os.makedirs(self.ordner)
        m = self.modell = FakeModell()
        m.ebene("EG- OK FFB", 0.0)
        m.ebene("1. OG - OK FFB", 3.0)
        # EG: Büro (20 m2), Aufzug (4 m2), Treppenhaus (32 m2), dazu Neubau-Raum und nicht platzierter Raum
        self.buero = m.raum("Raum", "n.1", "EG- OK FFB", (0, 0, 5, 4))
        self.aufzug = m.raum("Raum", "n.2", "EG- OK FFB", (6, 0, 8, 2))
        self.th = m.raum("Raum", "n.3", "EG- OK FFB", (0, 4, 8, 8))
        self.neubau = m.raum("Neu", "N.1", "EG- OK FFB", (0, 0, 5, 4), phase=m.neubau)
        self.leer = m.raum("Raum", "n.9", "EG- OK FFB", (0, 0, 0, 0), platziert=False)
        m.verknuepfung("100049_004_A_G00_Bestand.dwg", "EG- OK FFB", ursprung_ft=(VERSATZ_M / M_JE_FUSS, 0.0, 0.0))
        m.binde_parameter()
        m.installiere()
        # Stempel liegen in DWG-Koordinaten: Revit-x minus Versatz
        stempel_csv(os.path.join(self.ordner, "Stempel.csv"), [
            ("100049_004_A_G00.dwg", "20.20", "Büro", "1.01 ", "100049-004-A-G00-_01", 2.5 - VERSATZ_M, 2.0),
            ("100049_004_A_G00.dwg", "3.90", "Aufzug", "n.v.", "100049-004-A-G00-_02", 4.5 - VERSATZ_M, 5.0),
            ("100049_004_A_G00.dwg", "32.50", "Treppenhaus", "n.v.", "100049-004-A-G00-_03", 4.0 - VERSATZ_M, 6.0),
            # Zeile aus der externen Referenz: wird ignoriert
            ("100049_004_A_G00_Bestand.dwg", "99.00", "Fremd", "9.99", "100049-004-A-G00-_01", 0.0, 0.0),
            # Geschoss ohne Räume im Modell und unbekannter Geschosscode
            ("100049_004_A_G01.dwg", "10.00", "OG-Büro", "2.01", "100049-004-A-G01-_01", 1.0, 1.0),
            ("100049_004_A_G07.dwg", "10.00", "Weit weg", "7.01", "100049-004-A-G07-_01", 1.0, 1.0),
        ])

    def tearDown(self):
        self.modell.deinstalliere()
        self.tmp.cleanup()

    def lauf(self, **kw):
        return "\n".join(rs.haupt(eingaben(self.ordner, self.ausgabe, **kw)))

    def liste(self):
        dateien = sorted(f for f in os.listdir(self.ausgabe) if f.startswith("Zuordnungsliste_"))
        return os.path.join(self.ausgabe, dateien[-1])


class TestLauf1(AblaufBasis):
    def test_lauf1_ordnet_zu_und_schreibt_nichts(self):
        log = self.lauf()
        self.assertNotIn("FEHLER", log)
        self.assertIn("Modus: LAUF 1", log)
        self.assertIn("3 von 3 Stempeln liegen in einem Raum", log)       # Transformation mit Versatz stimmt
        self.assertIn("Koordinaten von Verknüpfung '100049_004_A_G00_Bestand.dwg'", log)
        self.assertIn("Plausibilität: 3 von 3", log)
        # Lauf 1 ändert nichts
        self.assertEqual(self.buero.name_wert, "Raum")
        self.assertEqual(self.buero.nummer_wert, "n.1")
        self.assertEqual(self.modell.doc.transaktionen, [])

    def test_zuordnungsliste_inhalt(self):
        self.lauf()
        tab = rs.lese_xlsx(self.liste(), "Zuordnung")
        zeilen = {r[1]: dict(zip(tab[0], r)) for r in tab[1:]}
        self.assertEqual(set(zeilen), {"100049-004-A-G00-_01", "100049-004-A-G00-_02", "100049-004-A-G00-_03"})
        self.assertEqual(int(zeilen["100049-004-A-G00-_01"]["Raum_ID"]), self.buero.Id.Value)
        self.assertEqual(int(zeilen["100049-004-A-G00-_02"]["Raum_ID"]), self.aufzug.Id.Value)   # über die Fläche
        self.assertEqual(int(zeilen["100049-004-A-G00-_03"]["Raum_ID"]), self.th.Id.Value)
        self.assertEqual({z["Status"] for z in zeilen.values()}, {"sicher"})
        self.assertEqual({z["Freigabe"] for z in zeilen.values()}, {"J"})
        # Zusatzblätter
        self.assertEqual(len(rs.lese_xlsx(self.liste(), "Stempel")), 1 + 3 + 2)   # G00 x3, G01, G07
        raeume = rs.lese_xlsx(self.liste(), "Räume")
        self.assertEqual(len(raeume), 1 + 4)                                      # 4 Räume der Phase Bestand
        self.assertNotIn(str(self.neubau.Id.Value), [r[1] for r in raeume[1:]])   # Neubau ignoriert

    def test_ebenen_und_ausschluss_meldungen(self):
        log = self.lauf()
        self.assertIn("1 Zeilen aus externen Referenzen ignoriert", log)
        self.assertIn("G01 -> 1. OG - OK FFB : 1 Stempel, Ebene ohne Räume", log)
        self.assertIn("G07 -> - : 1 Stempel, Geschosscode nicht in der Ebenenzuordnung", log)
        self.assertIn("Parameter RaumOKS und Raumnummer_Text sind an Räume gebunden", log)

    def test_prueflisten_datei(self):
        self.lauf()
        dateien = os.listdir(self.ausgabe)
        self.assertTrue(any(f.startswith("Pruefliste_") and f.endswith(".xlsx") for f in dateien))
        self.assertTrue(any(f.startswith("Zuordnungsliste_") and f.endswith(".xlsx") for f in dateien))

    def test_csv_und_beides(self):
        self.lauf(formate="beides")
        dateien = os.listdir(self.ausgabe)
        self.assertTrue(any(f.endswith(".csv") and f.startswith("Zuordnungsliste_") for f in dateien))
        self.assertTrue(any(f.endswith(".xlsx") and f.startswith("Zuordnungsliste_") for f in dateien))

    def test_ohne_verknuepfung_meldet_diagnose(self):
        from fake_revit import CADLinkType, ImportInstance
        # CAD-Elemente aus dem Modell entfernen
        self.modell.doc.elemente = [e for e in self.modell.doc.elemente
                                    if not isinstance(e, (CADLinkType, ImportInstance))]
        log = self.lauf()
        self.assertIn("FEHLER: Keine passende DWG-Verknüpfung", log)
        self.assertIn("CAD-Instanzen im Modell: 0", log)

    def test_falsche_phase_nennt_vorhandene(self):
        eing = eingaben(self.ordner, self.ausgabe)
        eing[5] = "Gibt es nicht"                                   # Position 5 = Phase
        log = "\n".join(rs.haupt(eing))
        self.assertIn("Phase 'Gibt es nicht' nicht im Projekt. Vorhandene Phasen: Bestand, Neubau", log)

    def test_fehlende_parameter_werden_in_lauf1_nur_gemeldet(self):
        self.modell.doc.ParameterBindings.eintraege = []
        log = self.lauf()
        self.assertIn("Fehlende Parameter an Räumen: RaumOKS, Raumnummer_Text", log)
        self.assertEqual(self.modell.doc.transaktionen, [])


class TestLauf2(AblaufBasis):
    def setUp(self):
        AblaufBasis.setUp(self)
        self.lauf()                                   # Lauf 1 erzeugt die Liste
        self.pfad = self.liste()

    def lauf2(self, liste=None, **kw):
        return "\n".join(rs.haupt(eingaben(self.ordner, self.ausgabe, trockenlauf=False,
                                           liste=liste or self.pfad, **kw)))

    def test_lauf2_schreibt_werte(self):
        log = self.lauf2()
        self.assertIn("Modus: LAUF 2", log)
        self.assertNotIn("FEHLER", log)
        self.assertEqual(self.buero.name_wert, "Büro")
        self.assertEqual(self.buero.nummer_wert, "G00-_01")
        self.assertEqual(self.buero.eigen_wert("RaumOKS"), "100049-004-A-G00-_01")
        self.assertEqual(self.buero.eigen_wert("Raumnummer_Text"), "Raum-Nr. 1.01")
        self.assertEqual(self.aufzug.name_wert, "Aufzug")
        self.assertEqual(self.aufzug.eigen_wert("Raumnummer_Text"), "Raum-Nr. n.v.")      # n.v. bleibt erhalten
        self.assertEqual(self.th.nummer_wert, "G00-_03")
        self.assertEqual(self.neubau.name_wert, "Neu")                                    # andere Phase unberührt
        self.assertEqual(self.leer.name_wert, "Raum")
        t = [x for x in self.modell.doc.transaktionen if x.name == "Raumstempel übertragen"]
        self.assertEqual([x.status for x in t], ["committed"])                             # genau eine Transaktion

    def test_lauf2_ist_wiederholbar(self):
        self.lauf2()
        log = self.lauf2()
        self.assertIn("Geschrieben: 0 Werte", log)
        self.assertIn("unverändert: 12", log)

    def test_trockenlauf_mit_liste_schreibt_nicht(self):
        log = "\n".join(rs.haupt(eingaben(self.ordner, self.ausgabe, trockenlauf=True, liste=self.pfad)))
        self.assertIn("nur Prüfung", log)
        self.assertEqual(self.buero.name_wert, "Raum")

    def dateien(self, praefix):
        return sorted(f for f in os.listdir(self.ausgabe) if f.startswith(praefix))

    def test_lauf2_nimmt_automatisch_die_neueste_liste(self):
        """Position 11 leer, Trockenlauf aus: kein Pfad nötig, keine neue Liste."""
        listen_vorher = self.dateien("Zuordnungsliste_")
        pruef_vorher = self.dateien("Pruefliste_")
        log = self.lauf(trockenlauf=False)
        self.assertIn("Modus: LAUF 2", log)
        self.assertIn("Zuordnungsliste (neueste im Ausgabeordner)", log)
        self.assertIn("zuletzt gespeichert am", log)
        self.assertNotIn("FEHLER", log)
        self.assertEqual(self.buero.name_wert, "Büro")
        self.assertEqual(self.dateien("Zuordnungsliste_"), listen_vorher)         # keine neue Liste
        self.assertEqual(self.dateien("Pruefliste_"), pruef_vorher)               # keine Auffälligkeiten -> keine Datei
        self.assertIn("keine Auffälligkeiten", log)

    def test_lauf2_nimmt_die_bearbeitete_liste(self):
        """Excel speichert unter demselben Namen: Lauf 2 liest die bearbeitete Fassung."""
        tab = rs.lese_xlsx(self.pfad, "Zuordnung")
        zeilen = [dict(zip(tab[0], r)) for r in tab[1:]]
        for z in zeilen:
            if z["OKS"].endswith("_02"):
                z["Freigabe"] = "N"
        rs.schreibe_xlsx(self.pfad, "Zuordnung", rs.ZUORDNUNG_SPALTEN, zeilen)
        self.lauf(trockenlauf=False)
        self.assertEqual(self.buero.name_wert, "Büro")
        self.assertEqual(self.aufzug.name_wert, "Raum")                           # in der Liste auf N gesetzt

    def test_lauf2_ohne_liste_im_ausgabeordner(self):
        for f in self.dateien("Zuordnungsliste_"):
            os.remove(os.path.join(self.ausgabe, f))
        log = self.lauf(trockenlauf=False)
        self.assertIn("FEHLER: Im Ausgabeordner liegt keine Zuordnungsliste", log)
        self.assertIn("Zuerst Lauf 1", log)
        self.assertEqual(self.buero.name_wert, "Raum")

    def test_lauf2_meldet_fehler_wenn_liste_in_excel_offen_ist(self):
        name = os.path.basename(self.pfad)
        with open(os.path.join(self.ausgabe, "~$" + name[2:]), "wb") as f:        # Excel-Sperrdatei
            f.write(b"x")
        log = self.lauf(trockenlauf=False)
        self.assertIn("ist noch in Excel geöffnet", log)
        self.assertIn(name, log)
        self.assertEqual(self.buero.name_wert, "Raum")
        self.assertFalse([x for x in self.modell.doc.transaktionen if x.name == "Raumstempel übertragen"])
        self.assertEqual(self.modell.doc.transaktionen, [])

    def test_lauf2_warnt_wenn_aeltere_liste_spaeter_gespeichert_wurde(self):
        neuere = os.path.join(self.ausgabe, "Zuordnungsliste_29991231_235959.xlsx")   # später erzeugt ...
        import shutil
        shutil.copy(self.pfad, neuere)
        import time
        t = time.time() - 7200
        os.utime(neuere, (t, t))                                                        # ... aber früher gespeichert
        log = self.lauf(trockenlauf=False)
        self.assertIn("WARNUNG: " + os.path.basename(self.pfad), log)
        self.assertIn("Zuordnungsliste_29991231_235959.xlsx", log)

    def test_expliziter_pfad_hat_vorrang(self):
        import shutil
        andere = os.path.join(self.tmp.name, "Zuordnungsliste_20000101_000000.xlsx")
        shutil.copy(self.pfad, andere)
        log = self.lauf2(liste=andere)
        self.assertIn(andere, log)
        self.assertNotIn("neueste im Ausgabeordner", log)

    def test_liste_nicht_vorhanden(self):
        log = self.lauf2(liste=os.path.join(self.tmp.name, "gibt_es_nicht.xlsx"))
        self.assertIn("FEHLER: Zuordnungsliste nicht gefunden", log)

    def test_oks_tausch_und_freigabe_n(self):
        """Stempel tauschen: Büro-Raum bekommt den Stempel des Aufzugs (OKS_Tausch), Aufzug-Raum bleibt N."""
        tab = rs.lese_xlsx(self.pfad, "Zuordnung")
        zeilen = [dict(zip(tab[0], r)) for r in tab[1:]]
        for z in zeilen:
            if z["OKS"].endswith("_01"):
                z["OKS_Tausch"] = "100049-004-A-G00-_02"
            if z["OKS"].endswith("_02"):
                z["Freigabe"] = "N"
        neu = os.path.join(self.tmp.name, "bearbeitet.xlsx")
        rs.schreibe_xlsx(neu, "Zuordnung", rs.ZUORDNUNG_SPALTEN, zeilen)
        log = self.lauf2(liste=neu)
        self.assertEqual(self.buero.name_wert, "Aufzug")
        self.assertEqual(self.buero.nummer_wert, "G00-_02")
        self.assertEqual(self.aufzug.name_wert, "Raum")             # nicht freigegeben
        self.assertNotIn("FEHLER", log)

    def test_unbekannte_tausch_oks_wird_nicht_geschrieben(self):
        tab = rs.lese_xlsx(self.pfad, "Zuordnung")
        zeilen = [dict(zip(tab[0], r)) for r in tab[1:]]
        zeilen[0]["OKS_Tausch"] = "100049-004-A-G00-_99"
        neu = os.path.join(self.tmp.name, "bearbeitet.xlsx")
        rs.schreibe_xlsx(neu, "Zuordnung", rs.ZUORDNUNG_SPALTEN, zeilen)
        log = self.lauf2(liste=neu)
        self.assertIn("steht in keiner Stempeldatei - nicht geschrieben", log)
        self.assertEqual(sum(1 for r in self.modell.raeume() if r.name_wert != "Raum" and r.name_wert != "Neu"), 2)

    def test_falsches_geschoss_und_doppelter_raum(self):
        tab = rs.lese_xlsx(self.pfad, "Zuordnung")
        zeilen = [dict(zip(tab[0], r)) for r in tab[1:]]
        zeilen[0]["OKS_Tausch"] = "100049-004-A-G01-_01"            # OG-Stempel in einem EG-Raum
        zeilen[1]["Raum_ID"] = zeilen[2]["Raum_ID"]                  # zwei Zeilen für denselben Raum
        neu = os.path.join(self.tmp.name, "bearbeitet.xlsx")
        rs.schreibe_xlsx(neu, "Zuordnung", rs.ZUORDNUNG_SPALTEN, zeilen)
        log = self.lauf2(liste=neu)
        self.assertIn("passt nicht zur Ebene", log)
        self.assertIn("steht mehrfach in der Liste", log)

    def test_oks_bereits_an_anderem_raum(self):
        self.lauf2()                                                  # alles geschrieben
        tab = rs.lese_xlsx(self.pfad, "Zuordnung")
        zeilen = [dict(zip(tab[0], r)) for r in tab[1:]]
        # Stempel _01 wird (nachträglich) dem Raum zugewiesen, in dem _03 steht: _01 steht schon am Büro
        for z in zeilen:
            if z["OKS"].endswith("_03"):
                z["OKS_Tausch"] = "100049-004-A-G00-_01"
        neu = os.path.join(self.tmp.name, "bearbeitet.xlsx")
        rs.schreibe_xlsx(neu, "Zuordnung", rs.ZUORDNUNG_SPALTEN, zeilen)
        log = self.lauf2(liste=neu)
        self.assertIn("steht schon an Raum", log)
        self.assertEqual(self.th.name_wert, "Treppenhaus")             # blieb unverändert

    def test_parameter_werden_in_lauf2_angelegt(self):
        self.modell.doc.ParameterBindings.eintraege = []
        for r in self.modell.raeume():
            r.eigene.clear()
        log = self.lauf2()
        self.assertIn("Parameter 'RaumOKS' gebunden", log)
        self.assertIn("NICHT aus der Firmendatei", log)
        self.assertEqual(self.modell.doc.Application.SharedParametersFilename, "")        # zurückgesetzt
        self.assertTrue(os.path.isfile(os.path.join(self.ausgabe, "Raumstempel_SharedParameters.txt")))
        namen = [d.Name for d, _b in self.modell.doc.ParameterBindings.eintraege]
        self.assertEqual(sorted(namen), ["RaumOKS", "Raumnummer_Text"])
        # die Räume haben die Parameter in diesem Fake nicht (Revit legt sie an) -> Schreiben meldet das
        self.assertIn("Parameter fehlt", log)


if __name__ == "__main__":
    unittest.main()
