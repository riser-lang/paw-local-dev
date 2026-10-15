import pystray
from PIL import Image, ImageDraw

def create_tray_image():
    # Generate a clean 64x64 icon: a cyan/blue rounded square with 'P'
    width = 64
    height = 64
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Draw rounded rectangle
    draw.rounded_rectangle([4, 4, 60, 60], radius=12, fill=(24, 144, 255))
    # Draw simple letter P or emblem
    draw.rectangle([18, 16, 26, 48], fill=(255, 255, 255))
    draw.rounded_rectangle([26, 16, 44, 34], radius=6, fill=(255, 255, 255))
    draw.rounded_rectangle([26, 22, 38, 28], radius=3, fill=(24, 144, 255))
    return image

img = create_tray_image()
img.save("paw_tray_icon.png")
print("Saved paw_tray_icon.png successfully")
