# Blender, headless, for the rig layer.
#
# Blender is the one dependency in this repo that cannot be a Python import and
# will not fit in a standard library: 400 MB of binary that has to be on PATH.
# Putting it in an image means the host needs Docker and nothing else, which is
# already true for the mesh engine.
#
#   docker build -f docker/Dockerfile -t text-to-3d/blender:5.2 .
#
# No GPU. Bone heat, the skinning solver this layer leans on, is CPU-only, and
# the export is CPU-only, so this image never touches /dev/dri. That is the
# whole reason it can be this plain.
#
# layers/assets uses the same image for its glTF to GLB conversion. It is tagged
# by Blender's own version rather than by a layer name for exactly that reason.
FROM ubuntu:24.04

ARG BLENDER_SERIES=5.2
ARG BLENDER_VERSION=5.2.0

# Blender links against X and GL even with --background, and refuses to start
# without them. These are the headless subset, not a desktop.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      xz-utils \
      libx11-6 \
      libxi6 \
      libxxf86vm1 \
      libxfixes3 \
      libxrender1 \
      libxkbcommon0 \
      libsm6 \
      libgl1 \
      libegl1 \
      libglu1-mesa \
      libfreetype6 \
      libpython3.12 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/blender.tar.xz \
      "https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
    && mkdir -p /opt/blender \
    && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
    && rm /tmp/blender.tar.xz \
    && ln -s /opt/blender/blender /usr/local/bin/blender

# The container runs as the caller's uid so the GLB it writes into the mounted
# work directory is owned by them and not by root. That leaves HOME unwritable,
# which Blender needs for its config, so it is pointed somewhere that always is.
ENV HOME=/tmp
ENV BLENDER_USER_RESOURCES=/tmp/blender-resources

# Fail the build rather than ship an image whose Blender cannot start.
RUN blender --background --factory-startup --python-expr "import bpy; print('blender ok', bpy.app.version_string)" \
    | grep -q "blender ok"

ENTRYPOINT ["blender"]
CMD ["--version"]
