#version 330 core
in vec3 fragColor;
in vec2 fragTexCoord;
out vec4 outColor;

uniform usampler2D vramTexture;   // VRAM atlas uploaded as an integer texture
uniform sampler2D paletteTex;     // Palette texture, size: 16 x num_palettes
uniform int paletteID;            // Which row (palette) to use
uniform bool polygonSemiTransparent;  // Example flag: if true then blend semi-transparent pixels

void main() {
    // Convert fragTexCoord (0-1) to integer texel coordinate for the atlas
    ivec2 atlasSize = textureSize(vramTexture, 0);
    ivec2 texelCoord = ivec2(floor(fragTexCoord * vec2(atlasSize)));
    
    // Fetch the palette index (0..15) as an unsigned int
    uint idx = texelFetch(vramTexture, texelCoord, 0).r;
    
    // Now use paletteID (set via uniform) to get the palette row from the palette texture.
    // We use texelFetch so that no interpolation occurs.
    ivec2 palSize = textureSize(paletteTex, 0); // Expected to be (16, N)
    vec4 palColor = texelFetch(paletteTex, ivec2(int(idx), paletteID), 0);
    
    // Handle transparency:
    // In many PS1 games index 0 is used for transparency. Also, your CLUT might use
    // a flag (stored in alpha) to indicate semi-transparency.
    // For this example, we assume that if palColor.alpha is near zero, the pixel is transparent.
    if (palColor.a < 0.1)
        discard;
        
    // Optionally, if polygonSemiTransparent is true, you might want to force alpha=0.5 for flagged pixels:
    // (Here you could add logic based on your data; for simplicity we assume palColor.a is already set.)
    
    // Multiply by vertex color and output final color.
    outColor = vec4(palColor.rgb * fragColor, palColor.a);
}