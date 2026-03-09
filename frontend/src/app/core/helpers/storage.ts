// Helpers functions

export function readBool(key: string, fallback: boolean): boolean {
  const v = localStorage.getItem(key);
  return v === null ? fallback : v === "true";
}

export function readStr(key: string, fallback: string): string {
  return localStorage.getItem(key) ?? fallback;
}
