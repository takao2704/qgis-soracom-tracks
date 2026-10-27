"""Build an allowlisted ZIP without runtime data."""
import argparse
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root=Path(__file__).resolve().parent
FILES=('compact_ui.py','__init__.py','plugin.py','dock.py','soracom_io.py','schema.py','schema_ui.py','schema_dialog.py','camera_bindings.py','camera_media.py','video_io.py','auth.py','auth_ui.py','metadata.txt','icon.svg')

EXTRA_FILES=('README.md','LICENSE','CHANGELOG.md','docs/DATA_FORMATS.md','docs/AUTHENTICATION.md','docs/RELEASE_VALIDATION.md')

def build(output):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(output,'w',ZIP_DEFLATED) as archive:
        for name in FILES:archive.write(root/'soracom_tracks'/name,'soracom_tracks/'+name)
        for name in EXTRA_FILES:archive.write(root/name,'soracom_tracks/'+name)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist())==len(FILES)+len(EXTRA_FILES)
    print(output)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    build(parser.parse_args().output)
