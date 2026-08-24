import { describe, expect, test } from "vitest";
import { getApiErrorMessage, getProblemDetailsMessage } from "../src/api/errors";

function axiosError(options: {
  data?: unknown;
  status?: number;
  code?: string;
  withResponse?: boolean;
}) {
  return {
    isAxiosError: true,
    code: options.code,
    response:
      options.withResponse === false
        ? undefined
        : {
            data: options.data,
            status: options.status ?? 400,
          },
  };
}

describe("getApiErrorMessage", () => {
  test("可以直接提取 Fetch 返回的 Problem Details", () => {
    expect(
      getProblemDetailsMessage({
        title: "权限不足",
        detail: "分配的 Doris 角色不可用",
      })
    ).toBe("分配的 Doris 角色不可用");
  });

  test("优先展示后端返回的详细说明", () => {
    const error = axiosError({
      data: {
        title: "角色已存在",
        detail: "Doris 角色 test 已存在",
      },
      status: 409,
    });

    expect(getApiErrorMessage(error, "创建角色失败")).toBe("Doris 角色 test 已存在");
  });

  test("详细说明缺失时展示后端标题", () => {
    const error = axiosError({
      data: { title: "角色已存在" },
      status: 409,
    });

    expect(getApiErrorMessage(error, "创建角色失败")).toBe("角色已存在");
  });

  test("无响应时区分超时和连接失败", () => {
    expect(
      getApiErrorMessage(axiosError({ code: "ECONNABORTED", withResponse: false }), "加载失败")
    ).toBe("请求超时，请稍后重试");
    expect(getApiErrorMessage(axiosError({ withResponse: false }), "加载失败")).toBe(
      "无法连接服务器，请检查网络连接或服务状态"
    );
  });

  test("响应体不是 Problem Details 时按状态码提示", () => {
    expect(getApiErrorMessage(axiosError({ status: 403 }), "操作失败")).toBe("没有权限执行此操作");
    expect(getApiErrorMessage(axiosError({ status: 500 }), "操作失败")).toBe(
      "服务器处理失败，请稍后重试"
    );
  });
});
