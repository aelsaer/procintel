import type {
  BaseRecord,
  CreateParams,
  CreateResponse,
  CustomParams,
  CustomResponse,
  DataProvider,
  GetListParams,
  GetListResponse,
  GetOneParams,
  GetOneResponse,
  HttpError,
  DeleteOneParams,
  DeleteOneResponse,
  UpdateParams,
  UpdateResponse,
} from "@refinedev/core";
import { ApiError, api, apiFetch, API_BASE_URL } from "@/lib/api";

function toHttpError(error: unknown): HttpError {
  if (error instanceof ApiError) {
    return {
      message: error.message,
      statusCode: error.status,
    };
  }

  if (error instanceof Error) {
    return {
      message: error.message,
      statusCode: 500,
    };
  }

  return {
    message: "Άγνωστο σφάλμα API.",
    statusCode: 500,
  };
}

function getFilterValue(filters: unknown, field: string): string | undefined {
  if (!Array.isArray(filters)) return undefined;

  const match = filters.find((filter) => {
    return typeof filter === "object" && filter !== null && "field" in filter && filter.field === field;
  });

  if (!match || typeof match !== "object" || !("value" in match)) return undefined;
  return typeof match.value === "string" ? match.value : undefined;
}

function withQuery(path: string, query: unknown): string {
  if (!query || typeof query !== "object") return path;

  const params = new URLSearchParams();
  Object.entries(query as Record<string, unknown>).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    params.set(key, String(value));
  });

  const serialized = params.toString();
  if (!serialized) return path;

  return `${path}${path.includes("?") ? "&" : "?"}${serialized}`;
}

async function unsupported(): Promise<never> {
  throw toHttpError(new ApiError(405, "Η ενέργεια δεν υποστηρίζεται από το δημόσιο API."));
}

async function getList<TData extends BaseRecord = BaseRecord>(
  params: GetListParams
): Promise<GetListResponse<TData>> {
  const { resource, pagination, filters, meta } = params;

  try {
    if (resource === "search") {
      const q = typeof meta?.q === "string" ? meta.q : getFilterValue(filters, "q");
      const cursor = typeof meta?.cursor === "string" ? meta.cursor : undefined;
      const limit = typeof meta?.limit === "number" ? meta.limit : pagination?.pageSize;

      if (!q?.trim()) {
        return { data: [] as TData[], total: 0 };
      }

      const response = await api.search(q.trim(), cursor, limit);

      return {
        data: response.data.map((item) => ({ ...item, id: item.act_id })) as unknown as TData[],
        total: response.pagination.has_more ? response.data.length + 1 : response.data.length,
        pagination: response.pagination,
      };
    }

    const listPaths: Record<string, string> = {
      pipeline: "/v1/workspace/pipeline",
      "alert-rules": "/v1/alert-rules",
      watches: "/v1/workspace/watches",
      exports: "/v1/exports",
      "saved-searches": "/v1/workspace/saved-searches",
    };
    if (listPaths[resource]) {
      const response = await apiFetch<TData[]>(listPaths[resource]);
      return { data: response, total: response.length };
    }

    throw new ApiError(404, `Unknown list resource: ${resource}`);
  } catch (error) {
    throw toHttpError(error);
  }
}

const collectionPaths: Record<string, string> = {
  pipeline: "/v1/workspace/pipeline",
  "alert-rules": "/v1/alert-rules",
  watches: "/v1/workspace/watches",
  exports: "/v1/exports",
  "saved-searches": "/v1/workspace/saved-searches",
};

async function create<TData extends BaseRecord = BaseRecord, TVariables = Record<string, unknown>>(
  params: CreateParams<TVariables>,
): Promise<CreateResponse<TData>> {
  const path = collectionPaths[params.resource];
  if (!path) return unsupported();
  try {
    const data = await apiFetch<TData>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params.variables),
    });
    return { data };
  } catch (error) {
    throw toHttpError(error);
  }
}

async function update<TData extends BaseRecord = BaseRecord, TVariables = Record<string, unknown>>(
  params: UpdateParams<TVariables>,
): Promise<UpdateResponse<TData>> {
  const path = collectionPaths[params.resource];
  if (!path) return unsupported();
  try {
    const data = await apiFetch<TData>(`${path}/${params.id}`, {
      method: params.resource === "pipeline" ? "PATCH" : "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params.variables),
    });
    return { data };
  } catch (error) {
    throw toHttpError(error);
  }
}

async function deleteOne<TData extends BaseRecord = BaseRecord, TVariables = Record<string, unknown>>(
  params: DeleteOneParams<TVariables>,
): Promise<DeleteOneResponse<TData>> {
  const path = collectionPaths[params.resource];
  if (!path) return unsupported();
  try {
    await apiFetch<void>(`${path}/${params.id}`, { method: "DELETE" });
    return { data: { id: params.id } as TData };
  } catch (error) {
    throw toHttpError(error);
  }
}

async function getOne<TData extends BaseRecord = BaseRecord>(
  params: GetOneParams
): Promise<GetOneResponse<TData>> {
  const { resource, id } = params;

  try {
    const key = String(id);

    if (resource === "contracts") {
      return { data: (await api.getContract(key)) as unknown as TData };
    }

    if (resource === "processes") {
      return { data: (await api.getProcess(key)) as unknown as TData };
    }

    if (resource === "process-timelines") {
      return { data: (await api.getProcessTimeline(key)) as unknown as TData };
    }

    if (resource === "buyers") {
      return { data: (await api.getBuyer(key)) as unknown as TData };
    }

    if (resource === "buyer-suppliers") {
      return { data: (await api.getBuyerSuppliers(key)) as unknown as TData };
    }

    if (resource === "companies") {
      return { data: (await api.getCompany(key)) as unknown as TData };
    }

    if (resource === "company-contracts") {
      return { data: (await api.getCompanyContracts(key)) as unknown as TData };
    }

    throw new ApiError(404, `Unknown detail resource: ${resource}`);
  } catch (error) {
    throw toHttpError(error);
  }
}

async function custom<TData extends BaseRecord = BaseRecord, TQuery = unknown, TPayload = unknown>(
  params: CustomParams<TQuery, TPayload>
): Promise<CustomResponse<TData>> {
  const { url, method, query, payload, headers } = params;

  try {
    const response = await apiFetch<TData>(withQuery(url, query), {
      method: method.toUpperCase(),
      headers: {
        ...(payload ? { "Content-Type": "application/json" } : {}),
        ...(headers ?? {}),
      },
      body: payload ? JSON.stringify(payload) : undefined,
    });

    return { data: response };
  } catch (error) {
    throw toHttpError(error);
  }
}

export const procurementDataProvider: DataProvider = {
  getApiUrl: () => API_BASE_URL,
  getList,
  getOne,
  custom,
  create,
  update,
  deleteOne,
};
