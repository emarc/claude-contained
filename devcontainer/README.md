# Devcontainer Template for Claude Contained

This template enables VS Code devcontainer workflow with the claude-contained image. Get full Java/Spring IDE features (IntelliSense, debugging) while also having Claude available in the terminal.

## What You Get

- Full Java IntelliSense via Eclipse JDT Language Server
- Debugging support for Java applications
- Spring Boot and Vaadin development tools
- Maven integration with cached dependencies
- `claude` command available in the integrated terminal
- **Path parity**: Your project stays at its original path (e.g., `/Users/you/project`, not `/workspaces/project`)

## Prerequisites

1. **Build the claude-contained image first:**
   ```bash
   docker build -t claude-contained .
   ```

2. **VS Code with Dev Containers extension installed**

3. **Required host directories** (created automatically by Claude Code):
   - `~/.claude` - Claude configuration
   - `~/.claude-contained` - Relocated credentials
   - `~/.m2` - Maven cache (optional, create if needed: `mkdir -p ~/.m2`)

## Usage

1. Copy this template to your project:
   ```bash
   cp -r /path/to/claude-contained/devcontainer /path/to/your-project/.devcontainer
   ```

2. Open your project in VS Code

3. When prompted, click "Reopen in Container" (or use Command Palette: "Dev Containers: Reopen in Container")

4. Wait for the container to start and extensions to install

5. Use Claude from the integrated terminal:
   ```bash
   claude
   ```

## Included VS Code Extensions

- **Extension Pack for Java** - Language support, debugging, testing, Maven
- **Spring Boot Extension Pack** - Spring Boot tools
- **Vaadin** - Vaadin development support
- **Lombok Annotations Support** - Lombok integration
- **GitLens** - Git supercharged

## Customization

### Adding More Mounts

Edit `devcontainer.json` to add additional bind mounts:

```json
"mounts": [
  // ... existing mounts ...
  "source=${localEnv:HOME}/.gradle,target=${localEnv:HOME}/.gradle,type=bind,consistency=cached"
]
```

### Changing Forwarded Ports

The template forwards ports 8080 (web app) and 5005 (debug). Modify as needed:

```json
"forwardPorts": [8080, 5005, 3000]
```

### Using a Different Java Version

The image includes JetBrains Runtime 25 (what the Vaadin Dev Loop prefers for hot reload). To use a different JDK, you would need to modify the Dockerfile and rebuild the image. The host's `~/.vaadin/jdk` is masked with `~/.claude-contained/vaadin-jdk-devcontainer`, so the Dev Loop never picks a macOS JDK from it and JDKs installed in the container stay out of the host's directory.

## Limitations

1. **Image must be pre-built**: Unlike Dockerfile-based devcontainers, this references a pre-built image

2. **Don't run simultaneously with standalone**: Avoid running this devcontainer while also running `claude-contained` or `claude-docked` with the same `~/.claude` directory

3. **UID/GID differences**: The devcontainer runs as the `dev` user. File permissions are generally handled well by VS Code, but you may see different ownership than on host

4. **host.local may not work**: VS Code manages container networking differently; services on the host may need explicit port forwarding

## Troubleshooting

### Java IntelliSense not working

1. Wait for the Java extension to finish initializing (watch the status bar)
2. Try "Java: Clean Java Language Server Workspace" from Command Palette
3. Ensure your project has a valid `pom.xml` or `build.gradle`

### Maven dependencies not resolving

Ensure `~/.m2` exists on your host and is properly mounted. Check the container logs for permission issues.

### Permission denied errors

If you see permission errors on files, the UID mismatch between host and container may be the cause. VS Code usually handles this, but you can try rebuilding the container.
