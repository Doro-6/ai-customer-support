const KEY = "dashboard-login";
export function saveLogin(username: string, password: string) {
  sessionStorage.setItem(KEY, btoa(`${username}:${password}`));
}
export function logout() {
  sessionStorage.removeItem(KEY);
}
export function isLoggedIn() {
  return sessionStorage.getItem(KEY) !== null;
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Basic ${sessionStorage.getItem(KEY) ?? ""}`,
      ...init.headers,
    },
  });
  if (response.status === 401) {
    logout();
    throw new Error("Wrong username or password");
  }
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}
