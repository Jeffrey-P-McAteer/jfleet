
# Arch Linux Dev Env Setup

```bash
# "Server" - knows about build stages and transforms toml => json => images
yay -S osbuild-composer

sudo systemctl enable --now osbuild-composer.socket
sudo systemctl enable --now osbuild-composer.service

# Client

go install github.com/osbuild/image-builder-cli/cmd/image-builder@main

image-builder list
image-builder list --filter 'arch:x86_64' --filter 'type:minimal-raw'
image-builder list --filter 'arch:x86_64' --filter 'type:qcow2'
```

# Building the Images

```bash
image-builder build qcow2 --arch x86_64 --blueprint jfleet-blueprint.toml --distro rhel-10.2 --progress verbose --output-dir build

image-builder build img --arch x86_64 --blueprint jfleet-blueprint.toml --distro rhel-10.2 --progress verbose --output-dir build

```


# Docs

 - https://osbuild.org/docs/user-guide/blueprint-reference/


