//! daytrace desktop shell: a menu-bar icon, an always-on-top day clock, and a day view window.
//! All data comes from the `daytrace` CLI (`daytrace day --json`), so stats live in one place.

use std::path::PathBuf;
use std::process::Command;

use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, PhysicalPosition, WindowEvent,
};

fn daytrace_bin() -> PathBuf {
    if let Ok(path) = std::env::var("DAYTRACE_BIN") {
        return path.into();
    }
    let home = std::env::var("HOME").unwrap_or_default();
    PathBuf::from(home).join(".local/bin/daytrace")
}

fn run_daytrace(args: &[&str]) -> Result<String, String> {
    // Apps started from Finder get a minimal PATH; `daytrace sync` needs git and gh.
    let path = format!(
        "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:{}",
        std::env::var("PATH").unwrap_or_default()
    );
    let bin = daytrace_bin();
    let out = Command::new(&bin)
        .args(args)
        .env("PATH", path)
        .output()
        .map_err(|e| format!("could not run {}: {e}", bin.display()))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

#[tauri::command]
async fn day(date: Option<String>) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut args = vec!["day", "--json"];
        if let Some(d) = date.as_deref() {
            args.extend(["--date", d]);
        }
        let text = run_daytrace(&args)?;
        serde_json::from_str(&text).map_err(|e| e.to_string())
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn period(kind: String, date: Option<String>) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut args = vec!["period", kind.as_str()];
        if let Some(d) = date.as_deref() {
            args.extend(["--date", d]);
        }
        let text = run_daytrace(&args)?;
        serde_json::from_str(&text).map_err(|e| e.to_string())
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sync_now(app: AppHandle) -> Result<String, String> {
    let _ = app.emit("syncing", ());
    let result = tauri::async_runtime::spawn_blocking(|| run_daytrace(&["sync"]))
        .await
        .map_err(|e| e.to_string())?;
    let _ = app.emit("synced", result.is_ok());
    result
}

#[tauri::command]
async fn summarize(
    app: AppHandle,
    date: Option<String>,
    send: bool,
    period: String,
    exclude: Option<String>,
) -> Result<String, String> {
    let result = tauri::async_runtime::spawn_blocking(move || {
        let mut args = vec!["summarize", period.as_str()];
        if let Some(d) = date.as_deref() {
            args.extend(["--date", d]);
        }
        if send {
            args.push("--send");
            if let Some(x) = exclude.as_deref() {
                args.extend(["--exclude", x]);
            }
        }
        run_daytrace(&args)
    })
    .await
    .map_err(|e| e.to_string())?;
    let _ = app.emit("synced", result.is_ok());
    result
}

#[tauri::command]
fn show_day(app: AppHandle, date: Option<String>) {
    show_main(&app, date);
}

fn show_main(app: &AppHandle, date: Option<String>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        if let Some(d) = date {
            let _ = window.emit("open-date", d);
        }
    }
}

fn toggle_pill(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("pill") {
        if window.is_visible().unwrap_or(false) {
            let _ = window.hide();
        } else {
            let _ = window.show();
        }
    }
}

/// Bottom-right corner of the primary screen, above the Dock.
fn place_pill(app: &AppHandle) {
    let Some(window) = app.get_webview_window("pill") else { return };
    let (Ok(Some(monitor)), Ok(size)) = (window.primary_monitor(), window.outer_size()) else { return };
    let margin = (20.0 * monitor.scale_factor()) as i32;
    let area = monitor.work_area();
    let x = area.position.x + area.size.width as i32 - size.width as i32 - margin;
    let y = area.position.y + area.size.height as i32 - size.height as i32 - margin;
    let _ = window.set_position(PhysicalPosition::new(x, y));
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![day, period, sync_now, summarize, show_day])
        .setup(|app| {
            // Menu-bar app: no Dock icon.
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            let open = MenuItem::with_id(app, "open", "Open day view", true, None::<&str>)?;
            let pill = MenuItem::with_id(app, "pill", "Show / hide floating clock", true, None::<&str>)?;
            let sync = MenuItem::with_id(app, "sync", "Sync now", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit daytrace", true, Some("cmd+q"))?;
            let separator = PredefinedMenuItem::separator(app)?;
            let menu = Menu::with_items(app, &[&open, &pill, &sync, &separator, &quit])?;

            TrayIconBuilder::with_id("daytrace")
                .icon(tauri::include_image!("icons/tray.png"))
                .icon_as_template(true)
                .tooltip("daytrace")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_main(app, None),
                    "pill" => toggle_pill(app),
                    "sync" => {
                        let app = app.clone();
                        tauri::async_runtime::spawn(async move {
                            let _ = sync_now(app).await;
                        });
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;

            place_pill(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing a window hides it; the app keeps running in the menu bar.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running daytrace");
}
