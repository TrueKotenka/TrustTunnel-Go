# TrustTunnel-Go

A Go wrapper for the TrustTunnel VPN library, providing cross-platform support for Windows, Linux, macOS, iOS, and Android.

Build workflows:
[Windows](https://github.com/DobbyVPN/go-go-tunnel/actions/workflows/build-windows.yml) |
[Linux](https://github.com/DobbyVPN/go-go-tunnel/actions/workflows/build-linux.yml) |
[macOS](https://github.com/DobbyVPN/go-go-tunnel/actions/workflows/build-macos.yml) |
[Android](https://github.com/DobbyVPN/go-go-tunnel/actions/workflows/build-android.yml) |
[iOS](https://github.com/DobbyVPN/go-go-tunnel/actions/workflows/build-ios.yml)

## Overview

TrustTunnel-Go provides a pure Go API wrapper around the TrustTunnel VPN protocol engine. It uses CGO to interface with platform-specific C++ implementations, allowing Go applications to leverage TrustTunnel's powerful VPN capabilities.

### Key Features

- **Pure Go API** - No need to import `"C"` in your application code
- **Cross-platform** - Windows, Linux, macOS, iOS, Android
- **Type-safe callbacks** - State changes, logging, and socket protection
- **Thread-safe** - All operations protected with mutexes
- **TOML configuration** - Simple, readable configuration format

## Architecture & Builds

The project uses GitHub Actions (`.github/workflows`) to provide automated builds for various platforms.
Build support is split into **Dynamic** and **Static** libraries:

- **Dynamic Builds**: Windows, Linux, macOS, Android
- **Static Builds**: macOS, Android, iOS

### Platform Guidelines

- **Mobile Platforms**: Static libraries are recommended. On Android, you might need `libc++_shared.so`, which can be found in the Android NDK.
- **Desktop Platforms (Windows, Linux)**: Support only dynamic builds because of ABI inconsistency. Static builds may be implemented in the future.

Static libraries are already committed in the repository, so they will work "out of the box". Dynamic libraries need to be downloaded separately if needed.

Public workflows are compile, export, and consumer-link checks and do not require connection credentials. Real-network qualification belongs to the consuming application's own environment.

## Quick Start

### Installation

1. **Update your `go.mod`:**
   Require the stable local module name and map it to this repository release:
   ```go
   require trusttunnel-go v0.0.0

   replace trusttunnel-go => github.com/DobbyVPN/go-go-tunnel v1.0.1
   ```

2. **Download the package:**
   ```bash
   go mod download trusttunnel-go
   ```

3. **Download dynamic library (if needed):**
   If your platform requires a dynamic library (e.g., Windows, Linux), download the pre-built library from GitHub Releases and place it in the appropriate directory for your application to link against. Static libs are included and work out of the box.

### Basic Example

```go
package main

import (
    "log"
    "time"
    
    tt "trusttunnel-go/manager"
)

func main() {
    // Create manager
    manager := tt.NewTrustTunnelManager()
    
    // Set up state change callback
    manager.SetStateChangedCallback(func(state tt.VpnState) {
        switch state {
        case tt.StateConnected:
            log.Println("VPN Connected!")
        case tt.StateDisconnected:
            log.Println("VPN Disconnected")
        case tt.StateError:
            log.Println("VPN Error")
        }
    })
    
    // Set up logging callback
    manager.SetLogCallback(func(level tt.LogLevel, message string) {
        log.Printf("[%v] %s", level, message)
    })
    
    // Optional: Custom socket protection
    manager.SetProtectSocketCallback(func(fd int) int {
        log.Printf("Protecting socket fd: %d", fd)
        return 0 // Return 0 on success
    })
    
    // Configure VPN
    config := `
[server]
address = "vpn.example.com"
port = 443

[credentials]
username = "user"
password = "pass"

[listener]
type = "socks"
address = "127.0.0.1:1080"

[upstream]
protocol = "http2"
location = "us-east"
`
    
    // Start VPN
    if err := manager.Start(config); err != nil {
        log.Fatal(err)
    }
    
    // Keep running
    log.Println("VPN is running. Press Ctrl+C to stop.")
    time.Sleep(30 * time.Second)
    
    // Stop VPN
    manager.Stop()
    log.Println("VPN stopped.")
}
```

## API Reference

### Types

- `VpnState` (StateDisconnected, StateConnecting, StateConnected, StateReconnecting, StateError)
- `LogLevel` (LogError, LogWarn, LogInfo, LogDebug, LogTrace)

### Callback Types

- `ProtectSocketFunc(fd int) int`: Called when a socket needs to be protected from the VPN tunnel.
- `StateChangedFunc(state VpnState)`: Called when the VPN connection state changes.
- `LogFunc(level LogLevel, message string)`: Called when the VPN library emits a log message.

### TrustTunnelManager Methods

- `NewTrustTunnelManager() *TrustTunnelManager`
- `SetStateChangedCallback(cb StateChangedFunc)`
- `SetLogCallback(cb LogFunc)`
- `SetProtectSocketCallback(cb ProtectSocketFunc)`
- `Start(tomlConfig string) error`
- `Stop()`

## Configuration

TrustTunnel uses TOML format for configuration. See the TrustTunnel documentation for complete configuration options.

### Minimal Configuration

```toml
[server]
address = "vpn.example.com"
port = 443

[credentials]
username = "your-username"
password = "your-password"

[listener]
type = "socks"
address = "127.0.0.1:1080"

[upstream]
protocol = "http2"
location = "us-east"
```

## Platform-Specific Notes

### macOS
- **TUN Device**: Uses `utun` device
- **Permissions**: Requires root privileges for TUN device creation
- **Socket Protection**: Uses `IP_BOUND_IF`/`IPV6_BOUND_IF` socket options

### Linux
- **TUN Device**: Uses TUN/TAP device (`/dev/net/tun`)
- **Permissions**: Requires `CAP_NET_ADMIN` capability or root privileges
- **Socket Protection**: Can use `SO_BINDTODEVICE` or network namespaces

### Windows
- **TUN Device**: Uses Wintun driver
- **Permissions**: Requires administrator privileges
- **Socket Protection**: Uses Wintun API

### iOS
- **Network Extension**: Uses NetworkExtension framework
- **Entitlements**: Requires proper entitlements (`com.apple.developer.networking.networkextension`)
- **Socket Protection**: Uses `IP_BOUND_IF`/`IPV6_BOUND_IF`

### Android
- **VPN Service**: Uses VpnService API
- **Permissions**: Requires `BIND_VPN_SERVICE` permission
- **Socket Protection**: Uses `VpnService.protect(fd)`

## Troubleshooting

### Runtime Issues

- **macOS: "dyld: Library not loaded"**
  Library not found at runtime. Set `DYLD_LIBRARY_PATH`, use `install_name_tool` to fix rpath, or copy the library next to the executable.

- **Linux: "error while loading shared libraries"**
  Library not in library search path. Set `LD_LIBRARY_PATH`, copy to system library directory, or use rpath during build.

- **Windows: "The code execution cannot proceed because dobby_bridge.dll was not found"**
  DLL not in executable directory or system PATH. Copy the DLL next to your executable or add its directory to your PATH.

## License

This project wraps the TrustTunnel library. Please refer to the TrustTunnel license for the underlying library terms.
