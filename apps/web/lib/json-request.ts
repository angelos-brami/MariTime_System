import "server-only";

type JsonRequestSuccess<T> = {
  ok: true;
  body: string;
  payload: T;
};

type JsonRequestFailure = {
  ok: false;
  response: Response;
};

export async function readJsonRequest<T>(
  request: Request,
  maxBytes: number,
  label: string,
): Promise<JsonRequestSuccess<T> | JsonRequestFailure> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json" && !contentType?.endsWith("+json")) {
    return {
      ok: false,
      response: Response.json(
        { detail: `${label} must use application/json` },
        { status: 415 },
      ),
    };
  }

  const declaredLength = request.headers.get("content-length");
  if (declaredLength && !/^\d+$/.test(declaredLength)) {
    return {
      ok: false,
      response: Response.json({ detail: "Invalid Content-Length header" }, { status: 400 }),
    };
  }
  if (declaredLength && Number(declaredLength) > maxBytes) {
    return {
      ok: false,
      response: Response.json({ detail: `${label} is too large` }, { status: 413 }),
    };
  }

  const body = await request.text();
  if (new TextEncoder().encode(body).byteLength > maxBytes) {
    return {
      ok: false,
      response: Response.json({ detail: `${label} is too large` }, { status: 413 }),
    };
  }

  try {
    const payload: unknown = JSON.parse(body);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new SyntaxError("JSON body must be an object");
    }
    return { ok: true, body, payload: payload as T };
  } catch {
    return {
      ok: false,
      response: Response.json({ detail: `${label} is not valid JSON` }, { status: 400 }),
    };
  }
}
