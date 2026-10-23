"""Write known snapshot results without replacing Excel formulas.

This is not a formula evaluator. Callers supply results from the same immutable
snapshot as the workbook. Run after the final openpyxl save (which drops caches).
"""
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZipFile
import os
import xml.etree.ElementTree as ET

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def write_formula_caches(path: Path, sheets: dict[str, dict[str, int | float | str]]) -> None:
    temporary: Path | None = None
    try:
        with ZipFile(path) as source:
            workbook = ET.fromstring(source.read('xl/workbook.xml'))
            rels = ET.fromstring(source.read('xl/_rels/workbook.xml.rels'))
            targets = {r.attrib['Id']: r.attrib['Target'] for r in rels}
            parts = {}
            for sheet in workbook.find(f'{{{MAIN}}}sheets'):
                if sheet.attrib['name'] in sheets:
                    target = targets[sheet.attrib[f'{{{REL}}}id']]
                    part = target.lstrip('/') if target.startswith('/') else 'xl/' + target
                    parts[part] = sheets[sheet.attrib['name']]
            if len(parts) != len(sheets):
                raise ValueError('Cache target sheet is missing')
            with NamedTemporaryFile(dir=path.parent, suffix='.xlsx', delete=False) as temp:
                temporary = Path(temp.name)
            with ZipFile(temporary, 'w') as output:
                for info in source.infolist():
                    data = source.read(info.filename)
                    if info.filename in parts:
                        root = ET.fromstring(data)
                        pending = dict(parts[info.filename])
                        for cell in root.iter(f'{{{MAIN}}}c'):
                            address = cell.attrib['r']
                            if address not in pending:
                                continue
                            value = pending.pop(address)
                            if cell.find(f'{{{MAIN}}}f') is None:
                                raise ValueError(f'Cache target is not a formula: {address}')
                            cell.set('t', 'str' if isinstance(value, str) else 'n')
                            cached = cell.find(f'{{{MAIN}}}v')
                            if cached is None:
                                cached = ET.SubElement(cell, f'{{{MAIN}}}v')
                            cached.text = str(value)
                        if pending:
                            raise ValueError(f'Cache target cells are missing: {sorted(pending)}')
                        data = ET.tostring(root, encoding='utf-8')
                    output.writestr(info, data)
        # Windows cannot replace a workbook while its ZIP reader is open.
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
