/** Browser notifications for things that finish while you're elsewhere (D143): a clipping job, a Short's
 *  build. Off until you turn it on in Settings (the browser asks permission then); only fires when this
 *  tab isn't the one you're looking at, since the page already shows a toast when it is. */
const KEY = "clipper.notify";

export const notifySupported = () => typeof Notification !== "undefined";

export function notifyOn(): boolean {
  try {
    return notifySupported() && localStorage.getItem(KEY) === "1" && Notification.permission === "granted";
  } catch {
    return false;
  }
}

/** Turn them on or off. Returns whether they're on now (false when the browser said no). */
export async function setNotify(on: boolean): Promise<boolean> {
  try {
    if (!on) { localStorage.setItem(KEY, "0"); return false; }
    if (!notifySupported()) return false;
    const permission = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
    localStorage.setItem(KEY, permission === "granted" ? "1" : "0");
    return permission === "granted";
  } catch {
    return false;
  }
}

export function notify(title: string, body: string, to = "/") {
  if (!notifyOn() || !document.hidden) return;
  const n = new Notification(title, { body, tag: `${title}|${body}` });
  n.onclick = () => { window.focus(); if (to) window.location.assign(to); n.close(); };
}
