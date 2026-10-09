# -*- coding: utf-8 -*-
"""Einfache Nachbildung der Revit-/Dynamo-Schnittstellen für Ablauftests ohne Revit.

Gebildet wird nur, was raumstempel_dynamo.py benutzt: Dokument mit Ebenen, Phasen, Räumen
(Rechtecke, damit IsPointInRoom funktioniert), CAD-Verknüpfungen, Parametern, Transaktionen und
Shared-Parameter-Datei. Echte Eigenheiten der Revit-API (pythonnet) werden nur teilweise
nachgestellt: z. B. wirft `Name` an CAD-Verknüpfungstypen "property cannot be read".
Verwendung: modell = FakeModell(); modell.installiere(); ...; modell.deinstalliere()
"""
import sys
import types

M_JE_FUSS = 0.3048
M2_JE_FT2 = 0.09290304


# --- Grundobjekte ----------------------------------------------------------------------------
class ElementId(object):
    def __init__(self, wert):
        self.Value = int(wert)

    def __eq__(self, other):
        return isinstance(other, ElementId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)


class XYZ(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.X, self.Y, self.Z = x, y, z


class Element(object):
    _zaehler = [1000]

    def __init__(self, name=""):
        Element._zaehler[0] += 1
        self.Id = ElementId(Element._zaehler[0])
        self._name = name

    @property
    def Name(self):
        return self._name


class Phase(Element):
    pass


class Level(Element):
    def __init__(self, name, hoehe_m):
        Element.__init__(self, name)
        self.Elevation = hoehe_m / M_JE_FUSS


class Category(object):
    def __init__(self, name, eid):
        self.Name = name
        self.Id = ElementId(eid)

    @staticmethod
    def GetCategory(doc, bic):
        return doc.kategorien[bic]


class Parameter(object):
    def __init__(self, wert="", schreibgeschuetzt=False):
        self._wert = wert
        self.IsReadOnly = schreibgeschuetzt
        self.StorageType = StorageType.String

    def AsString(self):
        return self._wert

    def AsElementId(self):
        return self._wert

    def Set(self, wert):
        self._wert = wert
        return True


class StorageType(object):
    String = "String"


class BuiltInParameter(object):
    ROOM_NAME = "ROOM_NAME"
    ROOM_NUMBER = "ROOM_NUMBER"
    ROOM_PHASE = "ROOM_PHASE"
    SYMBOL_NAME_PARAM = "SYMBOL_NAME_PARAM"
    ALL_MODEL_TYPE_NAME = "ALL_MODEL_TYPE_NAME"


class BuiltInCategory(object):
    OST_Rooms = "OST_Rooms"
    OST_RoomTags = "OST_RoomTags"


class Room(Element):
    """Raum als Rechteck (Meter) mit Phase, Ebene und Parametern."""

    def __init__(self, doc, name, nummer, ebene, rechteck_m, phase, platziert=True, flaeche_m2=None):
        Element.__init__(self, "%s %s" % (nummer, name))
        self.Category = doc.kategorien[BuiltInCategory.OST_Rooms]
        self.Level = ebene
        self.WorksetId = ElementId(1)
        x0, y0, x1, y1 = rechteck_m
        self._rechteck = (x0 / M_JE_FUSS, y0 / M_JE_FUSS, x1 / M_JE_FUSS, y1 / M_JE_FUSS)
        flaeche = flaeche_m2 if flaeche_m2 is not None else (x1 - x0) * (y1 - y0)
        self.Area = flaeche / M2_JE_FT2 if platziert else 0.0
        if platziert:
            self.Location = types.SimpleNamespace(
                Point=XYZ((self._rechteck[0] + self._rechteck[2]) / 2, (self._rechteck[1] + self._rechteck[3]) / 2, 0))
        else:
            self.Location = None
        self.parameter = {
            BuiltInParameter.ROOM_NAME: Parameter(name),
            BuiltInParameter.ROOM_NUMBER: Parameter(nummer),
            BuiltInParameter.ROOM_PHASE: Parameter(phase.Id),
        }
        self.eigene = {}      # Shared Parameter nach Name

    def get_Parameter(self, bip):
        return self.parameter.get(bip)

    def LookupParameter(self, name):
        return self.eigene.get(name)

    def IsPointInRoom(self, p):
        x0, y0, x1, y1 = self._rechteck
        z0 = self.Level.Elevation
        return x0 <= p.X <= x1 and y0 <= p.Y <= y1 and z0 <= p.Z <= z0 + 12.0

    # für Tests
    @property
    def name_wert(self):
        return self.parameter[BuiltInParameter.ROOM_NAME].AsString()

    @property
    def nummer_wert(self):
        return self.parameter[BuiltInParameter.ROOM_NUMBER].AsString()

    def eigen_wert(self, name):
        p = self.eigene.get(name)
        return p.AsString() if p is not None else None


class CADLinkType(Element):
    """Typ einer CAD-Verknüpfung: `Name` lässt sich (wie in Dynamo/CPython) nicht lesen."""

    @property
    def Name(self):
        raise TypeError("property cannot be read")


class ImportInstance(Element):
    def __init__(self, typ, ebene, verknuepft=True, ursprung_ft=(0.0, 0.0, 0.0), drehung90=False,
                 umriss_ft=(-100.0, -100.0, 500.0, 500.0)):
        Element.__init__(self, "")
        self._typ = typ
        self.LevelId = ebene.Id
        self.IsLinked = verknuepft
        self.Category = Category(typ._name, 0)
        o = ursprung_ft
        self._trafo = types.SimpleNamespace(
            Origin=XYZ(*o),
            BasisX=XYZ(0, 1, 0) if drehung90 else XYZ(1, 0, 0),
            BasisY=XYZ(-1, 0, 0) if drehung90 else XYZ(0, 1, 0),
            BasisZ=XYZ(0, 0, 1))
        self._umriss = umriss_ft

    def GetTypeId(self):
        return self._typ.Id

    def GetTotalTransform(self):
        return self._trafo

    def get_BoundingBox(self, ansicht):
        x0, y0, x1, y1 = self._umriss
        return types.SimpleNamespace(Min=XYZ(x0, y0, 0), Max=XYZ(x1, y1, 10))


class ViewPlan(Element):
    pass


class ViewSection(Element):
    pass


class RoomTag(Element):
    """Raumtag: verweist auf einen Raum, kein eigener Raum."""

    def __init__(self, raum, x_m, y_m, ansicht=None):
        Element.__init__(self, "Raumtag")
        self.View = ansicht or ViewPlan("Grundriss")
        self.Room = raum
        self.TagHeadPosition = XYZ(x_m / M_JE_FUSS, y_m / M_JE_FUSS, 0.0)


# --- Dokument -----------------------------------------------------------------------------------
class FilteredElementCollector(object):
    def __init__(self, doc):
        self._doc = doc
        self._liste = list(doc.elemente)

    def OfCategory(self, bic):
        klasse = {BuiltInCategory.OST_Rooms: Room, BuiltInCategory.OST_RoomTags: RoomTag}.get(bic)
        self._liste = [e for e in self._liste if klasse and isinstance(e, klasse)]
        return self

    def WhereElementIsNotElementType(self):
        return self

    def OfClass(self, klasse):
        self._liste = [e for e in self._liste if isinstance(e, klasse)]
        return self

    def __iter__(self):
        return iter(self._liste)


class _Bindungen(object):
    def __init__(self):
        self.eintraege = []     # (Definition, Bindung)

    def ForwardIterator(self):
        return _BindungsIterator(self.eintraege)

    def Insert(self, definition, bindung, gruppe):
        self.eintraege.append((definition, bindung))
        return True

    ReInsert = Insert


class _BindungsIterator(object):
    def __init__(self, eintraege):
        self._e = list(eintraege)
        self._i = -1

    def MoveNext(self):
        self._i += 1
        return self._i < len(self._e)

    @property
    def Key(self):
        return self._e[self._i][0]

    @property
    def Current(self):
        return self._e[self._i][1]


class _Kategorienmenge(object):
    def __init__(self):
        self._k = []

    def Insert(self, k):
        self._k.append(k)

    def Contains(self, k):
        return any(x is k for x in self._k)


class _Definitionen(list):
    def Create(self, opt):
        d = types.SimpleNamespace(Name=opt.name)
        self.append(d)
        return d


class _Gruppen(list):
    def Create(self, name):
        g = types.SimpleNamespace(Name=name, Definitions=_Definitionen())
        self.append(g)
        return g


class _Anwendung(object):
    def __init__(self):
        self.SharedParametersFilename = ""
        self.dateien = {}                         # Pfad -> Gruppen
        self.Create = types.SimpleNamespace(
            NewCategorySet=_Kategorienmenge,
            NewInstanceBinding=lambda cs: types.SimpleNamespace(Categories=cs))

    def OpenSharedParameterFile(self):
        pfad = self.SharedParametersFilename
        if not pfad:
            return None
        return types.SimpleNamespace(Groups=self.dateien.setdefault(pfad, _Gruppen()))


class Dokument(object):
    def __init__(self):
        self.Application = _Anwendung()
        self.elemente = []
        self.Phases = []
        self.IsWorkshared = False
        self.ParameterBindings = _Bindungen()
        self.kategorien = {BuiltInCategory.OST_Rooms: Category("Räume", -2000160)}
        self.Settings = types.SimpleNamespace(
            Categories=types.SimpleNamespace(get_Item=lambda bic: self.kategorien[bic]))
        self.transaktionen = []

    def GetElement(self, eid):
        for e in self.elemente + list(self.Phases):
            if e.Id == eid:
                return e
        return None

    def fuege_hinzu(self, e):
        self.elemente.append(e)
        return e


class Transaction(object):
    def __init__(self, doc, name):
        self._doc, self.name, self.status = doc, name, "neu"

    def Start(self):
        self.status = "offen"
        self._doc.transaktionen.append(self)

    def Commit(self):
        self.status = "committed"
        return TransactionStatus.Committed

    def RollBack(self):
        self.status = "zurueckgerollt"


class TransactionGroup(Transaction):
    def Assimilate(self):
        self.status = "assimiliert"


class TransactionStatus(object):
    Committed = "Committed"


class WorksharingUtils(object):
    @staticmethod
    def GetCheckoutStatus(doc, eid):
        return CheckoutStatus.OwnedByMe


class CheckoutStatus(object):
    OwnedByOtherUser = "OwnedByOtherUser"
    OwnedByMe = "OwnedByMe"


class ElementClassFilter(object):
    def __init__(self, klasse):
        self.klasse = klasse


class ExternalDefinitionCreationOptions(object):
    def __init__(self, name, spec):
        self.name, self.spec = name, spec


# --- Modell + Installation in sys.modules -------------------------------------------------------------
class FakeModell(object):
    """Baut ein kleines Revit-Projekt auf und meldet es als Revit-Module an."""

    def __init__(self):
        self.doc = Dokument()
        self.bestand = Phase("Bestand")
        self.neubau = Phase("Neubau")
        self.doc.Phases.extend([self.bestand, self.neubau])
        self.ebenen = {}
        self._gesichert = {}

    def ebene(self, name, hoehe_m):
        e = Level(name, hoehe_m)
        self.doc.fuege_hinzu(e)
        self.ebenen[name] = e
        return e

    def raum(self, name, nummer, ebene, rechteck_m, phase=None, **kw):
        r = Room(self.doc, name, nummer, self.ebenen[ebene], rechteck_m, phase or self.bestand, **kw)
        self.doc.fuege_hinzu(r)
        return r

    def raumtag(self, raum, x_m, y_m, ansicht=None):
        t = RoomTag(raum, x_m, y_m, ansicht)
        self.doc.fuege_hinzu(t)
        return t

    def verknuepfung(self, dateiname, ebene, **kw):
        t = CADLinkType(dateiname)
        self.doc.fuege_hinzu(t)
        i = ImportInstance(t, self.ebenen[ebene], **kw)
        self.doc.fuege_hinzu(i)
        return i

    def binde_parameter(self):
        """RaumOKS / Raumnummer_Text sind bereits an Räume gebunden (Normalfall nach dem ersten Schreiblauf)."""
        for n in ("RaumOKS", "Raumnummer_Text"):
            cs = _Kategorienmenge()
            cs.Insert(self.doc.kategorien[BuiltInCategory.OST_Rooms])
            self.doc.ParameterBindings.Insert(types.SimpleNamespace(Name=n), types.SimpleNamespace(Categories=cs), None)
            for r in self.doc.elemente:
                if isinstance(r, Room):
                    r.eigene[n] = Parameter("")

    def raeume(self):
        return [e for e in self.doc.elemente if isinstance(e, Room)]

    def installiere(self):
        modul = types.ModuleType
        db = modul("Autodesk.Revit.DB")
        for n, o in dict(ElementId=ElementId, XYZ=XYZ, Element=Element, Level=Level, Category=Category,
                         StorageType=StorageType, BuiltInParameter=BuiltInParameter,
                         BuiltInCategory=BuiltInCategory, ViewPlan=ViewPlan, ImportInstance=ImportInstance,
                         CADLinkType=CADLinkType, FilteredElementCollector=FilteredElementCollector,
                         Transaction=Transaction, TransactionGroup=TransactionGroup,
                         TransactionStatus=TransactionStatus, WorksharingUtils=WorksharingUtils,
                         CheckoutStatus=CheckoutStatus, ElementClassFilter=ElementClassFilter,
                         ExternalDefinitionCreationOptions=ExternalDefinitionCreationOptions).items():
            setattr(db, n, o)
        db.SpecTypeId = types.SimpleNamespace(String=types.SimpleNamespace(Text="Text"))
        db.GroupTypeId = types.SimpleNamespace(Data="Daten")
        revit = modul("Autodesk.Revit")
        revit.DB = db
        autodesk = modul("Autodesk")
        autodesk.Revit = revit
        clr = modul("clr")
        clr.AddReference = lambda name: None
        persist = modul("RevitServices.Persistence")
        persist.DocumentManager = types.SimpleNamespace(
            Instance=types.SimpleNamespace(CurrentDBDocument=self.doc))
        trans = modul("RevitServices.Transactions")
        trans.TransactionManager = types.SimpleNamespace(
            Instance=types.SimpleNamespace(ForceCloseTransaction=lambda: None))
        rs = modul("RevitServices")
        rs.Persistence, rs.Transactions = persist, trans
        neu = {"Autodesk": autodesk, "Autodesk.Revit": revit, "Autodesk.Revit.DB": db, "clr": clr,
               "RevitServices": rs, "RevitServices.Persistence": persist, "RevitServices.Transactions": trans}
        for k, v in neu.items():
            self._gesichert[k] = sys.modules.get(k)
            sys.modules[k] = v

    def deinstalliere(self):
        for k, v in self._gesichert.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        self._gesichert = {}
