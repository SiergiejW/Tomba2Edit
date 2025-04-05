from PIL import Image
import os

# Path to the iconset image
input_image_path = r"icons\tomba\test\iconset.png"

# Directory to save the individual icons
output_directory = r"icons\tomba\test"

# Ensure the output directory exists
os.makedirs(output_directory, exist_ok=True)

# Open the image
image = Image.open(input_image_path)

# Get the dimensions of the image
width, height = image.size

# Number of icons horizontally and vertically
icons_horizontal = 14
icons_vertical = 13

# Calculate the width and height of each icon
icon_width = width // icons_horizontal
icon_height = height // icons_vertical

# Counter for naming the icons
icon_counter = 1

# Loop through each icon and crop it
for y in range(icons_vertical):
    for x in range(icons_horizontal):
        # Calculate the bounding box for the current icon, excluding the 1-pixel border
        left = x * icon_width + 1  # Skip 1 pixel from the left
        upper = y * icon_height + 1  # Skip 1 pixel from the top
        right = (x + 1) * icon_width - 1  # Skip 1 pixel from the right
        lower = (y + 1) * icon_height - 1  # Skip 1 pixel from the bottom

        # Crop the icon
        icon = image.crop((left, upper, right, lower))

        # Save the icon with the alpha channel
        icon.save(os.path.join(output_directory, f"icon{icon_counter}.png"), "PNG")

        # Increment the counter
        icon_counter += 1

print(f"All icons have been saved to {output_directory}")