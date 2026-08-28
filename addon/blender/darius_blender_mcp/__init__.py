"""DariusAI Blender bridge — an MCP server that runs inside Blender.

Install this add-on, enable it, and DariusAI drives Blender over MCP on
127.0.0.1:8765. The server runs on a daemon thread; every tool call is
marshalled back onto Blender's main thread before it touches `bpy`.

Ships with DariusAI: Settings → Blender → Install add-on writes it into
Blender's add-ons directory and enables it, so there is nothing to clone
or pip-install.
"""

from __future__ import annotations

import traceback

bl_info = {
    "name": "DariusAI Blender MCP",
    "author": "DariusAI",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "Edit > Preferences > Add-ons > DariusAI Blender MCP",
    "description": "Model Context Protocol server so DariusAI can drive Blender.",
    "category": "System",
}

import bpy  # noqa: E402
from bpy.props import BoolProperty, IntProperty, StringProperty  # noqa: E402
from bpy.types import AddonPreferences, Operator  # noqa: E402

from .server.mcp_server import get_server, registry  # noqa: E402
from .server.protocol import SERVER_NAME, SERVER_VERSION  # noqa: E402

PACKAGE = __name__


def _prefs():
    try:
        return bpy.context.preferences.addons[PACKAGE].preferences
    except (KeyError, AttributeError):
        return None


class DARIUS_MCP_Preferences(AddonPreferences):
    bl_idname = PACKAGE

    host: StringProperty(
        name="Host", default="127.0.0.1",
        description="Interface to bind. Leave on loopback unless you know why not.",
    )
    port: IntProperty(
        name="Port", default=8765, min=1024, max=65535,
        description="TCP port for the MCP server. DariusAI looks here by default.",
    )
    auth_token: StringProperty(
        name="Auth token", default="", subtype="PASSWORD",
        description="Optional bearer token. Leave empty on a single-user machine.",
    )
    auto_start: BoolProperty(
        name="Start automatically", default=True,
        description="Start the server when Blender loads this add-on.",
    )

    def draw(self, context):
        layout = self.layout
        server = get_server()

        row = layout.row()
        row.label(text=f"{SERVER_NAME} {SERVER_VERSION} — {len(registry)} tools")
        row.label(text="running" if server.running else "stopped",
                  icon="CHECKMARK" if server.running else "X")

        column = layout.column()
        column.prop(self, "host")
        column.prop(self, "port")
        column.prop(self, "auth_token")
        column.prop(self, "auto_start")

        buttons = layout.row(align=True)
        buttons.operator(DARIUS_MCP_Start.bl_idname, icon="PLAY")
        buttons.operator(DARIUS_MCP_Stop.bl_idname, icon="PAUSE")
        buttons.operator(DARIUS_MCP_Restart.bl_idname, icon="FILE_REFRESH")

        if server.running:
            layout.label(text=f"Endpoint: {server.endpoint}", icon="URL")


class DARIUS_MCP_Start(Operator):
    bl_idname = "darius_mcp.start"
    bl_label = "Start"
    bl_description = "Start the MCP server"

    def execute(self, context):
        prefs = _prefs()
        if prefs is None:
            self.report({"ERROR"}, "add-on preferences unavailable")
            return {"CANCELLED"}
        try:
            get_server().start(prefs.host, prefs.port, prefs.auth_token)
        except OSError as exc:
            # Overwhelmingly the common failure: something else has 8765.
            self.report({"ERROR"}, f"could not bind {prefs.host}:{prefs.port} — {exc}")
            return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, f"failed to start: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"MCP server listening on {get_server().endpoint}")
        return {"FINISHED"}


class DARIUS_MCP_Stop(Operator):
    bl_idname = "darius_mcp.stop"
    bl_label = "Stop"
    bl_description = "Stop the MCP server"

    def execute(self, context):
        get_server().stop()
        self.report({"INFO"}, "MCP server stopped")
        return {"FINISHED"}


class DARIUS_MCP_Restart(Operator):
    bl_idname = "darius_mcp.restart"
    bl_label = "Restart"
    bl_description = "Restart the MCP server, picking up the host/port above"

    def execute(self, context):
        get_server().stop()
        return DARIUS_MCP_Start.execute(self, context)


CLASSES = (DARIUS_MCP_Preferences, DARIUS_MCP_Start, DARIUS_MCP_Stop, DARIUS_MCP_Restart)


def _auto_start():
    """Deferred via a timer: preferences are not readable at register()
    time during Blender's start-up, so asking then returns None."""
    prefs = _prefs()
    if prefs is None:
        return 1.0  # not ready yet — ask again in a second
    if prefs.auto_start and not get_server().running:
        try:
            get_server().start(prefs.host, prefs.port, prefs.auth_token)
            print(f"[{SERVER_NAME}] listening on {get_server().endpoint}")
        except Exception as exc:
            print(f"[{SERVER_NAME}] auto-start failed: {exc}")
    return None  # unregister the timer


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)

    # Import the tool modules so their decorators run. Wrapped: a broken
    # tool file must not make the add-on unloadable, because then the user
    # cannot even open preferences to see what happened.
    try:
        from . import tools  # noqa: F401
    except Exception:
        traceback.print_exc()

    bpy.app.timers.register(_auto_start, first_interval=0.5)


def unregister():
    try:
        get_server().stop()
    except Exception:
        pass
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
