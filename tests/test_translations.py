"""Translated labels merge onto the real nodes as ``label_<locale>`` attrs;
translations of unretrieved targets survive as ``partial`` nodes."""
from graphbuilder import build_graph


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _repo(tmp_path):
    fa = tmp_path / "force-app" / "main" / "default"
    obj = fa / "objects" / "MeterPoint__c"
    _w(obj / "MeterPoint__c.object-meta.xml",
       "<CustomObject><label>Meter Point</label></CustomObject>")
    _w(obj / "fields" / "Reading__c.field-meta.xml",
       "<CustomField><fullName>Reading__c</fullName><type>Number</type></CustomField>")
    _w(obj / "recordTypes" / "Standard.recordType-meta.xml",
       "<RecordType><fullName>Standard</fullName></RecordType>")
    tdir = fa / "objectTranslations" / "MeterPoint__c-pl"
    _w(tdir / "MeterPoint__c-pl.objectTranslation-meta.xml",
       """<CustomObjectTranslation>
            <caseValues><plural>false</plural><value>Punkt pomiarowy</value></caseValues>
            <caseValues><plural>true</plural><value>Punkty pomiarowe</value></caseValues>
            <recordTypes><name>Standard</name><label>Standardowy</label></recordTypes>
          </CustomObjectTranslation>""")
    _w(tdir / "Reading__c.fieldTranslation-meta.xml",
       "<CustomFieldTranslation><name>Reading__c</name><label>Odczyt</label>"
       "</CustomFieldTranslation>")
    _w(fa / "labels" / "CustomLabels.labels-meta.xml",
       "<CustomLabels><labels><fullName>Greeting</fullName><value>Hello</value>"
       "<language>en_US</language></labels></CustomLabels>")
    _w(fa / "translations" / "pl.translation-meta.xml",
       """<Translations>
            <customLabels><label>Witaj</label><name>Greeting</name></customLabels>
            <quickActions><label>Nowy odczyt</label><name>LogReading</name></quickActions>
          </Translations>""")
    return tmp_path


def test_translations_merge_onto_real_nodes(tmp_path):
    g = build_graph(_repo(tmp_path))
    by_id = {n["id"]: n for n in g["nodes"]}

    fld = by_id["field/MeterPoint__c.Reading__c"]
    assert fld["label_pl"] == "Odczyt"
    assert fld["field_type"] == "Number"           # real attrs survived the merge
    assert "partial" not in fld                    # real node owns the identity
    assert fld["source_path"].endswith("fields/Reading__c.field-meta.xml")

    assert by_id["object/MeterPoint__c"]["label_pl"] == "Punkt pomiarowy"
    assert by_id["object/MeterPoint__c"]["label"] == "Meter Point"
    assert by_id["recordtype/MeterPoint__c.Standard"]["label_pl"] == "Standardowy"
    assert by_id["label/Greeting"]["label_pl"] == "Witaj"
    assert by_id["label/Greeting"]["language"] == "en_US"


def test_translation_without_target_stays_partial(tmp_path):
    g = build_graph(_repo(tmp_path))
    by_id = {n["id"]: n for n in g["nodes"]}
    # LogReading quick action was never retrieved — its translation survives,
    # honestly flagged as a donor without a real counterpart
    qa = by_id["quickaction/LogReading"]
    assert qa["label_pl"] == "Nowy odczyt" and qa.get("partial") is True


def test_locale_attr_is_normalized(tmp_path):
    fa = tmp_path / "force-app" / "main" / "default"
    _w(fa / "translations" / "en_US.translation-meta.xml",
       "<Translations><customLabels><label>Hi</label><name>Greeting</name>"
       "</customLabels></Translations>")
    g = build_graph(tmp_path)
    n = next(x for x in g["nodes"] if x["id"] == "label/Greeting")
    assert n["label_en_us"] == "Hi"
