"""One-time owner configuration, separate from the regular ROC interface."""
import os

from .ai_credentials import ENV_NAME, WindowsCredentialStore, validate_key
from .common import RocError


def main():
    import tkinter as tk
    from tkinter import ttk, messagebox
    root = tk.Tk()
    root.title('ROC — Owner AI setup')
    root.minsize(560, 420)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Connect the ROC account once', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Owner setup only. Regular Document Grabber users do not enter keys.\n'
              'Model usage is billed to this Anthropic account.', wraplength=500).pack(anchor='w', pady=(12, 16))
    ttk.Label(frame, text='Project Anthropic API key').pack(anchor='w')
    entry = ttk.Entry(frame, show='•', width=60)
    entry.pack(fill='x', pady=(5, 8)); entry.focus_set()
    visible = tk.BooleanVar(value=False)
    ttk.Checkbutton(frame, text='Show key', variable=visible,
                    command=lambda: entry.configure(show='' if visible.get() else '•')).pack(anchor='w')
    status = ttk.Label(frame, text='Saved in Windows Credential Manager for this Windows account.\n'
                       'ROC loads it automatically after restarts. No API request is made here.', wraplength=500)
    status.pack(anchor='w', pady=12)
    controls = ttk.Frame(frame); controls.pack(fill='x', pady=6)

    def save():
        try:
            if ENV_NAME in os.environ:
                raise RocError('ROC_ANTHROPIC_API_KEY is already configured on this host. Update that backend setting or remove it before using the local credential store.')
            key = validate_key(entry.get().strip())
            WindowsCredentialStore().save(key)
            entry.delete(0, 'end')
            status.configure(text='Saved. ROC will use the shared account automatically.\n'
                             'You can close this window. No analysis or charge was submitted.')
        except RocError as exc:
            entry.delete(0, 'end')
            status.configure(text=str(exc))
        except Exception:
            entry.delete(0, 'end')
            status.configure(text='Owner setup could not save the credential. No plaintext fallback was written.')

    def remove():
        if not messagebox.askyesno('Remove ROC account?', 'Remove the saved AI credential from this Windows account?', parent=root):
            return
        try:
            WindowsCredentialStore().remove()
            entry.delete(0, 'end'); status.configure(text='Saved ROC credential removed.')
        except RocError as exc:
            status.configure(text=str(exc))

    ttk.Button(controls, text='Save ROC account', command=save).pack(side='left')
    ttk.Button(controls, text='Remove saved key', command=remove).pack(side='left', padx=8)
    ttk.Button(controls, text='Close', command=root.destroy).pack(side='right')
    root.update_idletasks()
    root.geometry(f'{max(560, root.winfo_reqwidth())}x{max(420, root.winfo_reqheight())}')
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
