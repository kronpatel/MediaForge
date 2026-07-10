import os
import datetime
import customtkinter as ctk
from tkinter import filedialog, messagebox

# ACCENT COLORS
_CLR_BG         = "#0f1117"
_CLR_SURFACE    = "#1a1d27"
_CLR_CARD       = "#20232f"
_CLR_BORDER     = "#2e3347"
_CLR_TEXT       = "#e8eaf0"
_CLR_SUBTEXT    = "#8b92a8"
_CLR_ACCENT     = "#4f8ef7"
_CLR_ACCENT_HOV = "#3a76e8"

# Maps for Mode display names to keys
MODE_MAP = {
    "Audio (MP3)": "mp3",
    "Video (1080p)": "1080p",
    "Video (4K)": "4k",
    "Video (8K)": "8k",
    "Playlist (MP3)": "playlist_mp3",
    "Playlist (Video)": "playlist_video"
}

REV_MODE_MAP = {v: k for k, v in MODE_MAP.items()}

class ScheduleDialog(ctk.CTkToplevel):
    def __init__(self, master: any, schedule_data: dict | None = None, on_save: callable = None) -> None:
        super().__init__(master)
        self.on_save = on_save
        self.schedule_data = schedule_data
        
        self.title("Schedule Download" if not schedule_data else "Edit Schedule")
        self.geometry("450x520")
        self.resizable(False, False)
        self.configure(fg_color=_CLR_BG)
        self.transient(master)
        self.grab_set()
        
        # Center dialog
        x = master.winfo_x() + (master.winfo_width() - 450) // 2
        y = master.winfo_y() + (master.winfo_height() - 520) // 2
        self.geometry(f"450x520+{x}+{y}")
        
        self._build_ui()
        self._load_data()

    def _build_ui(self) -> None:
        # Title Label
        lbl_title = ctk.CTkLabel(
            self,
            text="Schedule Download" if not self.schedule_data else "Edit Schedule Details",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color=_CLR_TEXT
        )
        lbl_title.pack(pady=(16, 12), padx=20, anchor="w")
        
        # Form Container
        self._form = ctk.CTkFrame(self, fg_color="transparent")
        self._form.pack(fill="both", expand=True, padx=20, pady=10)
        
        # 1. URL Entry
        self._create_label(self._form, "Download URL")
        self._url_var = ctk.StringVar()
        self._url_entry = ctk.CTkEntry(
            self._form,
            textvariable=self._url_var,
            placeholder_text="Enter YouTube or supported media URL",
            height=30,
            fg_color=_CLR_SURFACE,
            border_color=_CLR_BORDER,
            corner_radius=6
        )
        self._url_entry.pack(fill="x", pady=(0, 10))

        # 2. Type/Mode Dropdown
        self._create_label(self._form, "Download Type / Quality")
        self._mode_var = ctk.StringVar(value="Video (1080p)")
        self._mode_menu = ctk.CTkOptionMenu(
            self._form,
            variable=self._mode_var,
            values=list(MODE_MAP.keys()),
            height=30,
            fg_color=_CLR_SURFACE,
            button_color=_CLR_BORDER,
            button_hover_color=_CLR_BORDER,
            dropdown_fg_color=_CLR_SURFACE,
            dropdown_hover_color=_CLR_BORDER,
            corner_radius=6
        )
        self._mode_menu.pack(fill="x", pady=(0, 10))

        # 3. Output Folder (Picker)
        self._create_label(self._form, "Custom Download Folder (Optional)")
        folder_row = ctk.CTkFrame(self._form, fg_color="transparent")
        folder_row.pack(fill="x", pady=(0, 10))
        
        self._folder_var = ctk.StringVar()
        self._folder_entry = ctk.CTkEntry(
            folder_row,
            textvariable=self._folder_var,
            placeholder_text="Default downloads folder",
            height=30,
            fg_color=_CLR_SURFACE,
            border_color=_CLR_BORDER,
            corner_radius=6
        )
        self._folder_entry.pack(side="left", fill="x", expand=True)
        
        btn_browse = ctk.CTkButton(
            folder_row,
            text="Browse",
            width=70,
            height=30,
            fg_color=_CLR_CARD,
            hover_color=_CLR_BORDER,
            text_color=_CLR_TEXT,
            command=self._browse_folder,
            corner_radius=6
        )
        btn_browse.pack(side="left", padx=(8, 0))

        # 4. Date & Time Pickers (Row)
        dt_row = ctk.CTkFrame(self._form, fg_color="transparent")
        dt_row.pack(fill="x", pady=(0, 10))
        
        date_col = ctk.CTkFrame(dt_row, fg_color="transparent")
        date_col.pack(side="left", fill="x", expand=True)
        self._create_label(date_col, "Date (YYYY-MM-DD)")
        self._date_var = ctk.StringVar(value=datetime.date.today().strftime("%Y-%m-%d"))
        self._date_entry = ctk.CTkEntry(
            date_col,
            textvariable=self._date_var,
            placeholder_text="YYYY-MM-DD",
            height=30,
            fg_color=_CLR_SURFACE,
            border_color=_CLR_BORDER,
            corner_radius=6
        )
        self._date_entry.pack(fill="x", pady=(0, 2))
        
        time_col = ctk.CTkFrame(dt_row, fg_color="transparent")
        time_col.pack(side="left", fill="x", expand=True, padx=(12, 0))
        self._create_label(time_col, "Time (HH:MM:SS)")
        future_time = (datetime.datetime.now() + datetime.timedelta(minutes=5)).strftime("%H:%M:%S")
        self._time_var = ctk.StringVar(value=future_time)
        self._time_entry = ctk.CTkEntry(
            time_col,
            textvariable=self._time_var,
            placeholder_text="HH:MM:SS",
            height=30,
            fg_color=_CLR_SURFACE,
            border_color=_CLR_BORDER,
            corner_radius=6
        )
        self._time_entry.pack(fill="x", pady=(0, 2))

        # 5. Repeat & Retries Row
        opts_row = ctk.CTkFrame(self._form, fg_color="transparent")
        opts_row.pack(fill="x", pady=(0, 10))

        rep_col = ctk.CTkFrame(opts_row, fg_color="transparent")
        rep_col.pack(side="left", fill="x", expand=True)
        self._create_label(rep_col, "Repeat Type")
        self._repeat_var = ctk.StringVar(value="One Time")
        self._repeat_menu = ctk.CTkOptionMenu(
            rep_col,
            variable=self._repeat_var,
            values=["One Time", "Daily", "Weekly", "Monthly"],
            height=30,
            fg_color=_CLR_SURFACE,
            button_color=_CLR_BORDER,
            button_hover_color=_CLR_BORDER,
            dropdown_fg_color=_CLR_SURFACE,
            dropdown_hover_color=_CLR_BORDER,
            corner_radius=6
        )
        self._repeat_menu.pack(fill="x")

        ret_col = ctk.CTkFrame(opts_row, fg_color="transparent")
        ret_col.pack(side="left", fill="x", expand=True, padx=(12, 0))
        self._create_label(ret_col, "Retry Limit (0-5)")
        self._retry_var = ctk.StringVar(value="2")
        self._retry_entry = ctk.CTkEntry(
            ret_col,
            textvariable=self._retry_var,
            placeholder_text="2",
            height=30,
            fg_color=_CLR_SURFACE,
            border_color=_CLR_BORDER,
            corner_radius=6
        )
        self._retry_entry.pack(fill="x")

        # 6. Enable Checkbox
        self._enabled_var = ctk.BooleanVar(value=True)
        self._enabled_chk = ctk.CTkCheckBox(
            self._form,
            variable=self._enabled_var,
            text="Enable this schedule",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=_CLR_TEXT,
            border_width=2,
            corner_radius=4
        )
        self._enabled_chk.pack(anchor="w", pady=(8, 10))

        # Bottom Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom", pady=20, padx=20)
        
        btn_cancel = ctk.CTkButton(
            btn_frame,
            text="Cancel",
            width=100,
            height=32,
            fg_color=_CLR_CARD,
            hover_color=_CLR_BORDER,
            text_color=_CLR_TEXT,
            command=self.destroy,
            corner_radius=8
        )
        btn_cancel.pack(side="left")
        
        btn_save = ctk.CTkButton(
            btn_frame,
            text="Save Schedule",
            width=140,
            height=32,
            fg_color=_CLR_ACCENT,
            hover_color=_CLR_ACCENT_HOV,
            text_color="#ffffff",
            command=self._save_click,
            corner_radius=8
        )
        btn_save.pack(side="right")

    def _create_label(self, parent: ctk.CTkFrame, text: str) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=_CLR_SUBTEXT,
            anchor="w"
        ).pack(fill="x", pady=(2, 2))

    def _browse_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=os.path.expanduser("~/Downloads"))
        if selected:
            self._folder_var.set(selected)

    def _load_data(self) -> None:
        if not self.schedule_data:
            return
            
        data = self.schedule_data
        self._url_var.set(data.get("url", ""))
        self._mode_var.set(REV_MODE_MAP.get(data.get("mode"), "Video (1080p)"))
        self._folder_var.set(data.get("output_folder", ""))
        
        sched_time_str = data.get("scheduled_time", "")
        if sched_time_str:
            try:
                dt = datetime.datetime.strptime(sched_time_str, "%Y-%m-%d %H:%M:%S")
                self._date_var.set(dt.strftime("%Y-%m-%d"))
                self._time_var.set(dt.strftime("%H:%M:%S"))
            except Exception:
                pass
                
        self._repeat_var.set(data.get("repeat_type", "One Time"))
        self._retry_var.set(str(data.get("max_retries", 2)))
        self._enabled_var.set(bool(data.get("enabled", True)))

    def _save_click(self) -> None:
        url = self._url_var.get().strip()
        mode = MODE_MAP.get(self._mode_var.get())
        folder = self._folder_var.get().strip()
        date_str = self._date_var.get().strip()
        time_str = self._time_var.get().strip()
        repeat = self._repeat_var.get()
        retry_str = self._retry_var.get().strip()
        enabled = self._enabled_var.get()

        # Validations
        if not url:
            messagebox.showerror("Error", "Download URL is required.", parent=self)
            return
            
        if not url.startswith(("http://", "https://")):
            messagebox.showerror("Error", "Please enter a valid HTTP/HTTPS URL.", parent=self)
            return

        if folder and not os.path.isdir(folder):
            messagebox.showerror("Error", "The specified output folder does not exist.", parent=self)
            return

        # Parse date and time
        try:
            scheduled_time_str = f"{date_str} {time_str}"
            scheduled_dt = datetime.datetime.strptime(scheduled_time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            messagebox.showerror("Error", "Invalid Date or Time format. Use YYYY-MM-DD and HH:MM:SS.", parent=self)
            return

        if repeat == "One Time" and scheduled_dt <= datetime.datetime.now() and not self.schedule_data:
            messagebox.showerror("Error", "Scheduled execution time must be in the future.", parent=self)
            return

        try:
            retries = int(retry_str)
            if retries < 0 or retries > 5:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Retry Count must be an integer between 0 and 5.", parent=self)
            return

        # Build payload
        payload = {
            "url": url,
            "mode": mode,
            "quality": self._mode_var.get(),
            "output_folder": folder,
            "scheduled_time": scheduled_time_str,
            "repeat_type": repeat,
            "max_retries": retries,
            "enabled": enabled
        }
        
        if self.schedule_data:
            payload["uuid"] = self.schedule_data["uuid"]
            
        if self.on_save:
            self.on_save(payload)
            
        self.destroy()
