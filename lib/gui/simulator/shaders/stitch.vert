#version 330

layout(location = 0) in vec2 position;

uniform mat4 u_projection;
uniform mat4 u_transform;

void main() {
    gl_Position = u_projection * u_transform * vec4(position, 0.0, 1.0);
}
