"""Testy pro io_module.xlsx_reader — stdlib read-only XLSX reader."""
import io
import zipfile

import pytest

from io_module.xlsx_reader import XlsxError, _column_index, read_xlsx
from tests.xtb_fixtures import Num, build_xlsx


def _raw_xlsx(sheet_xml: str, shared_xml: str = None) -> bytes:
    """XLSX s ručně zadaným XML listu (pro inline/rich text a řádky bez atributu r)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("xl/workbook.xml",
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels",
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        zf.writestr("xl/worksheets/sheet1.xml",
                    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    f"<sheetData>{sheet_xml}</sheetData></worksheet>")
        if shared_xml is not None:
            zf.writestr("xl/sharedStrings.xml",
                        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                        f"{shared_xml}</sst>")
    return buffer.getvalue()


class TestReadXlsx:
    def test_text_a_cisla_presne_jako_text(self):
        book = read_xlsx(build_xlsx({"List": [("Ticker", "Volume"), ("TST1.US", Num("0.1")),
                                              ("TST2.US", Num("1E-3"))]}))
        rows = book.sheets["List"]
        assert rows[1] == {1: "Ticker", 2: "Volume"}
        assert rows[2] == {1: "TST1.US", 2: "0.1"}        # čísla zůstávají textem, žádný float
        assert rows[3][2] == "1E-3"
        assert book.date1904 is False

    def test_vice_listu_a_date1904(self):
        book = read_xlsx(build_xlsx({"A": [("x",)], "B": [("y",)]}, date1904=True))
        assert list(book.sheets) == ["A", "B"]
        assert book.date1904 is True

    def test_prazdne_bunky_se_vynechaji(self):
        rows = read_xlsx(build_xlsx({"S": [("a", "", "c")]})).sheets["S"]
        assert rows[1] == {1: "a", 3: "c"}

    def test_inline_a_rich_text(self):
        xml = ('<row r="1"><c r="A1" t="inlineStr"><is><t>inline</t></is></c>'
               '<c r="B1" t="s"><v>0</v></c></row>')
        shared = "<si><r><t>rich </t></r><r><t>text</t></r></si>"
        assert read_xlsx(_raw_xlsx(xml, shared)).sheets["S"][1] == {1: "inline", 2: "rich text"}

    def test_radky_a_bunky_bez_atributu_r(self):
        xml = "<row><c><v>1</v></c><c><v>2</v></c></row><row><c><v>3</v></c></row>"
        assert read_xlsx(_raw_xlsx(xml)).sheets["S"] == {1: {1: "1", 2: "2"}, 2: {1: "3"}}

    def test_neplatny_zip(self):
        with pytest.raises(XlsxError):
            read_xlsx(b"not a zip")

    def test_chybejici_cast_workbooku(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("x.txt", "x")
        with pytest.raises(XlsxError, match="workbook.xml"):
            read_xlsx(buffer.getvalue())

    def test_neplatny_index_sdileneho_retezce(self):
        with pytest.raises(XlsxError):
            read_xlsx(_raw_xlsx('<row r="1"><c r="A1" t="s"><v>5</v></c></row>', "<si><t>a</t></si>"))

    @pytest.mark.parametrize("letters, index", [("A", 1), ("Z", 26), ("AA", 27), ("AZ", 52), ("BA", 53)])
    def test_index_sloupce(self, letters, index):
        assert _column_index(letters) == index
