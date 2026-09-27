import axios, { type AxiosError } from "axios";
import type { components } from "@/api/generated";
import { clearSelection, getSelectedUserId, redirectToUserSelection } from "@/identity";

type ProblemDetails = components["schemas"]["ProblemDetails"];
const appClient = axios.create({ timeout: 15000 });

appClient.interceptors.request.use(
  (config) => {
    const userId = getSelectedUserId();
    if (userId) config.headers["X-User-ID"] = userId;
    if (config.data instanceof FormData) delete config.headers["Content-Type"];
    else config.headers["Content-Type"] = "application/json";
    return config;
  },
  undefined,
  { synchronous: true }
);

appClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ProblemDetails>) => {
    if (
      error.response?.status === 401 &&
      error.config?.headers["X-User-ID"] === getSelectedUserId()
    ) {
      clearSelection();
      redirectToUserSelection();
    }
    return Promise.reject(error);
  }
);

export default appClient;
