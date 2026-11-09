"""Convert the supplied logos to Windows icon and toolbar display sizes."""
from pathlib import Path

from PIL import Image


def prepare_icons():
    folder = Path(__file__).resolve().parents[2] / 'wrapitupiso/resources/icons'
    with Image.open(folder / 'nuke logo.png') as image:
        image.save(folder / 'app.ico', sizes=[(size, size) for size in
                                            (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    with Image.open(folder / 'foundry-nuke-logo-png_seeklogo-397183.png') as image:
        image.resize((85, 30), Image.Resampling.LANCZOS).save(folder / 'wordmark.png')
        image.resize((170, 60), Image.Resampling.LANCZOS).save(folder / 'wordmark_2x.png')


if __name__ == '__main__':
    prepare_icons()
