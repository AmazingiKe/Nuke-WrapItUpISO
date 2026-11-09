"""Regenerate archived Tk toolbar PNGs; unused by the current Qt interface."""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / 'icons'
SCALE = 4

def render(name, color):
    image = Image.new('RGBA', (20 * SCALE, 20 * SCALE))
    draw = ImageDraw.Draw(image)
    def line(points, width=1.5):
        draw.line([(round(x*SCALE), round(y*SCALE)) for x,y in points], fill=color,
                  width=round(width*SCALE), joint='curve')
    def rect(box):
        draw.rectangle(tuple(round(v*SCALE) for v in box), outline=color, width=6)
    if name == 'add':
        line([(10,4),(10,16)]); line([(4,10),(16,10)])
    elif name == 'clear':
        line([(4,6),(16,6)]); line([(7,6),(7,3),(13,3),(13,6)])
        line([(5,6),(6,17),(14,17),(15,6)])
        line([(8,9),(8,14)],1); line([(12,9),(12,14)],1)
    elif name == 'scan':
        draw.ellipse((3*SCALE,3*SCALE,12*SCALE,12*SCALE), outline=color, width=6)
        line([(11,11),(17,17)])
    elif name == 'pack':
        line([(6,3),(16,10),(6,17),(6,3)])
    elif name == 'stop':
        rect((4,4,16,16))
    return image.resize((20,20), Image.Resampling.LANCZOS)

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ('add','clear','scan','pack','stop'):
        for suffix, color in (('', '#334155'), ('_disabled', '#a1a1aa')):
            render(name, color).save(OUT / f'{name}{suffix}.png')
