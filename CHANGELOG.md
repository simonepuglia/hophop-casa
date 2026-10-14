# Changelog

## 1.0.1

Fix cancellation of the HA reload HTTP request when it traverses the tunnel being unloaded. Shield the bounded cleanup and complete the config-entry unload before HA starts its replacement. Schedule options reloads through HA’s scheduler. Include integration domain, entry state and task cancellation in diagnostics and lifecycle regression tests. Each installation still needs its own device token and active subscription.

## 1.0.0

First public HACS release of HopHop Casa.
