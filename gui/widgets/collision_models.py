"""Render solid collision models with their own depth before overlay fading."""
import numpy as np
from OpenGL import GL
from PyQt6.QtGui import QImage
from PyQt6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject


class NativeModelPass:
    def __init__(self):
        self.size = None
        self.fbo = GL.glGenFramebuffers(1)
        self.color, self.depth, self.scene_depth = GL.glGenTextures(3)
        self.vao = QOpenGLVertexArrayObject()
        self.vao.create()
        self.program = QOpenGLShaderProgram()
        assert self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, """
            #version 330 core
            void main() {
                vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
                gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
            }
        """), self.program.log()
        assert self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, """
            #version 330 core
            uniform sampler2D modelColor;
            uniform sampler2D modelDepth;
            uniform sampler2D sceneDepth;
            uniform mat4 inverseMVP;
            uniform vec3 materialColor;
            uniform vec2 viewportOrigin;
            out vec4 outColor;
            void main() {
                ivec2 p = ivec2(gl_FragCoord.xy - viewportOrigin);
                vec4 color = texelFetch(modelColor, p, 0);
                if (color.a == 0.0) discard;
                float z = texelFetch(modelDepth, p, 0).r;
                float scene = texelFetch(sceneDepth, p, 0).r;
                float opacity = 1.0;
                if (z > scene + 0.000002) {
                    vec2 ndc = (vec2(p) + 0.5) / vec2(textureSize(modelDepth, 0)) * 2.0 - 1.0;
                    vec4 world = inverseMVP * vec4(ndc, z * 2.0 - 1.0, 1.0);
                    float distance = abs(1.0 / world.w);
                    opacity = 0.5 / (1.0 + pow(distance / 12.0, 2.0));
                }
                outColor = vec4(color.rgb * materialColor, color.a * opacity);
            }
        """), self.program.log()
        assert self.program.link(), self.program.log()
        from icons.icons import texture_collision_arrows
        image = QImage(texture_collision_arrows)
        if image.isNull():
            raise ValueError('Bundled collision-arrow texture is missing.')
        color = image.pixelColor(0, 0)
        self.material = color.redF(), color.greenF(), color.blueF()

    @staticmethod
    def _texture(texture, internal, width, height, pixel_format, pixel_type):
        GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, internal, width, height, 0,
                        pixel_format, pixel_type, None)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)

    def draw(self, source_program, model_vao, count):
        viewport = tuple(int(v) for v in GL.glGetIntegerv(GL.GL_VIEWPORT))
        x, y, width, height = viewport
        draw_fbo = int(GL.glGetIntegerv(GL.GL_DRAW_FRAMEBUFFER_BINDING))
        read_fbo = int(GL.glGetIntegerv(GL.GL_READ_FRAMEBUFFER_BINDING))
        active = int(GL.glGetIntegerv(GL.GL_ACTIVE_TEXTURE))
        textures = []
        for unit in range(3):
            GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
            textures.append(int(GL.glGetIntegerv(GL.GL_TEXTURE_BINDING_2D)))
        depth_on = bool(GL.glIsEnabled(GL.GL_DEPTH_TEST))
        blend_on = bool(GL.glIsEnabled(GL.GL_BLEND))
        depth_write = bool(GL.glGetBooleanv(GL.GL_DEPTH_WRITEMASK))
        depth_func = int(GL.glGetIntegerv(GL.GL_DEPTH_FUNC))
        clear_color = GL.glGetFloatv(GL.GL_COLOR_CLEAR_VALUE)
        stencil_on = bool(GL.glIsEnabled(GL.GL_STENCIL_TEST))
        scissor_on = bool(GL.glIsEnabled(GL.GL_SCISSOR_TEST))
        saved_uniforms = {}
        for name in ('hiddenCount', 'keepCount', 'useFlat', 'useTextures'):
            location = source_program.uniformLocation(name)
            if location >= 0:
                value = np.empty(1, dtype=np.int32)
                GL.glGetUniformiv(source_program.programId(), location, value)
                saved_uniforms[name] = int(value[0])
        matrix = np.empty(16, dtype=np.float32)
        GL.glGetUniformfv(source_program.programId(), source_program.uniformLocation('modelViewProjection'), matrix)
        inverse = np.linalg.inv(matrix.reshape(4, 4, order='F')).astype(np.float32)
        try:
            # Main-model selection masks must not clip this independent model.
            for name in ('hiddenCount', 'keepCount'):
                if name in saved_uniforms:
                    source_program.setUniformValue(name, 0)
            source_program.setUniformValue('useFlat', False)
            source_program.setUniformValue('useTextures', False)
            GL.glDisable(GL.GL_STENCIL_TEST)
            GL.glDisable(GL.GL_SCISSOR_TEST)
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, draw_fbo)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.scene_depth)
            GL.glCopyTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_DEPTH_COMPONENT24, x, y, width, height, 0)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
            if self.size != (width, height):
                # Float colour retains source channels >1 until final texture
                # modulation and framebuffer clamping, like the SMST renderer.
                self._texture(self.color, GL.GL_RGBA16F, width, height, GL.GL_RGBA, GL.GL_FLOAT)
                self._texture(self.depth, GL.GL_DEPTH_COMPONENT24, width, height, GL.GL_DEPTH_COMPONENT, GL.GL_FLOAT)
                GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, self.color, 0)
                GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_TEXTURE_2D, self.depth, 0)
                if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
                    raise RuntimeError('Collision model framebuffer is incomplete.')
                self.size = width, height
            GL.glViewport(0, 0, width, height)
            GL.glDepthMask(GL.GL_TRUE)
            GL.glEnable(GL.GL_DEPTH_TEST)
            GL.glDepthFunc(GL.GL_LESS)
            GL.glDisable(GL.GL_BLEND)
            GL.glClearColor(0, 0, 0, 0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
            source_program.setUniformValue('alpha', 1.0)
            source_program.setUniformValue('hiddenFadeRange', 0.0)
            model_vao.bind()
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, count)
            model_vao.release()
            GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, draw_fbo)
            GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, read_fbo)
            GL.glViewport(*viewport)
            self.program.bind()
            for unit, (name, texture) in enumerate((('modelColor', self.color), ('modelDepth', self.depth), ('sceneDepth', self.scene_depth))):
                GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
                GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
                self.program.setUniformValue(name, unit)
            from PyQt6.QtGui import QVector2D, QVector3D
            self.program.setUniformValue('materialColor', QVector3D(*self.material))
            self.program.setUniformValue('viewportOrigin', QVector2D(x, y))
            GL.glUniformMatrix4fv(self.program.uniformLocation('inverseMVP'), 1, GL.GL_TRUE, inverse)
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDepthMask(GL.GL_FALSE)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            self.vao.bind()
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
            self.vao.release()
        finally:
            GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, draw_fbo)
            GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, read_fbo)
            GL.glViewport(*viewport)
            GL.glDepthMask(depth_write)
            GL.glDepthFunc(depth_func)
            (GL.glEnable if depth_on else GL.glDisable)(GL.GL_DEPTH_TEST)
            (GL.glEnable if blend_on else GL.glDisable)(GL.GL_BLEND)
            GL.glClearColor(*clear_color)
            for unit, texture in enumerate(textures):
                GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
                GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
            GL.glActiveTexture(active)
            source_program.bind()
            for name, value in saved_uniforms.items():
                source_program.setUniformValue(name, bool(value) if name.startswith('use') else value)
            (GL.glEnable if stencil_on else GL.glDisable)(GL.GL_STENCIL_TEST)
            (GL.glEnable if scissor_on else GL.glDisable)(GL.GL_SCISSOR_TEST)
