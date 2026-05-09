import { create } from "zustand";
import type {
  FilterState,
  PermissionSortField,
  RoleSortField,
  SortState,
  UserSortField,
} from "@/features/permission/types";
import { toggleSort } from "@/features/permission/types";

interface PermissionStore {
  filter: FilterState;
  setFilter: (updater: FilterState | ((prev: FilterState) => FilterState)) => void;
  clearFilter: () => void;

  userSearch: string;
  setUserSearch: (s: string) => void;
  roleSearch: string;
  setRoleSearch: (s: string) => void;
  permissionSearch: string;
  setPermissionSearch: (s: string) => void;

  userSort: SortState<UserSortField>;
  toggleUserSort: (field: UserSortField) => void;
  roleSort: SortState<RoleSortField>;
  toggleRoleSort: (field: RoleSortField) => void;
  permissionSort: SortState<PermissionSortField>;
  togglePermissionSort: (field: PermissionSortField) => void;

  // User dialog state
  createUserOpen: boolean;
  setCreateUserOpen: (v: boolean) => void;
  createUserLoading: boolean;
  setCreateUserLoading: (v: boolean) => void;
  editUserOpen: boolean;
  setEditUserOpen: (v: boolean) => void;
  editUserLoading: boolean;
  setEditUserLoading: (v: boolean) => void;
  editUserId: number | null;
  setEditUserId: (v: number | null) => void;
  editUserUsername: string;
  setEditUserUsername: (v: string) => void;
  editUserEmail: string;
  setEditUserEmail: (v: string) => void;
  editUserYn: number;
  setEditUserYn: (v: number) => void;
  editUserRoles: number[];
  setEditUserRoles: (v: number[]) => void;
  originalUserRoles: number[];
  setOriginalUserRoles: (v: number[]) => void;
  editUserRelationOpen: boolean;
  setEditUserRelationOpen: (v: boolean) => void;

  // Role dialog state
  createRoleOpen: boolean;
  setCreateRoleOpen: (v: boolean) => void;
  createRoleLoading: boolean;
  setCreateRoleLoading: (v: boolean) => void;
  editRoleOpen: boolean;
  setEditRoleOpen: (v: boolean) => void;
  editRoleLoading: boolean;
  setEditRoleLoading: (v: boolean) => void;
  editRoleId: number | null;
  setEditRoleId: (v: number | null) => void;
  editRoleName: string;
  setEditRoleName: (v: string) => void;
  editRoleYn: number;
  setEditRoleYn: (v: number) => void;
  editRoleUsers: number[];
  setEditRoleUsers: (v: number[]) => void;
  originalRoleUsers: number[];
  setOriginalRoleUsers: (v: number[]) => void;
  editRolePermissions: number[];
  setEditRolePermissions: (v: number[]) => void;
  originalRolePermissions: number[];
  setOriginalRolePermissions: (v: number[]) => void;
  editRoleRelationOpen: boolean;
  setEditRoleRelationOpen: (v: boolean) => void;
  editRoleRelationTab: "users" | "permissions";
  setEditRoleRelationTab: (v: "users" | "permissions") => void;

  // Permission dialog state
  createPermissionOpen: boolean;
  setCreatePermissionOpen: (v: boolean) => void;
  createPermissionLoading: boolean;
  setCreatePermissionLoading: (v: boolean) => void;
  editPermissionOpen: boolean;
  setEditPermissionOpen: (v: boolean) => void;
  editPermissionLoading: boolean;
  setEditPermissionLoading: (v: boolean) => void;
  editPermissionId: number | null;
  setEditPermissionId: (v: number | null) => void;
  editPermissionName: string;
  setEditPermissionName: (v: string) => void;
  editPermissionDesc: string;
  setEditPermissionDesc: (v: string) => void;
  editPermissionYn: number;
  setEditPermissionYn: (v: number) => void;
  editPermissionRoles: number[];
  setEditPermissionRoles: (v: number[]) => void;
  originalPermissionRoles: number[];
  setOriginalPermissionRoles: (v: number[]) => void;
  editPermissionRelationOpen: boolean;
  setEditPermissionRelationOpen: (v: boolean) => void;
}

export const usePermissionStore = create<PermissionStore>()((set) => ({
  filter: { userId: null, roleId: null, permissionId: null },
  setFilter: (updater) =>
    set((state) => ({
      filter: typeof updater === "function" ? updater(state.filter) : updater,
    })),
  clearFilter: () => set({ filter: { userId: null, roleId: null, permissionId: null } }),

  userSearch: "",
  setUserSearch: (userSearch) => set({ userSearch }),
  roleSearch: "",
  setRoleSearch: (roleSearch) => set({ roleSearch }),
  permissionSearch: "",
  setPermissionSearch: (permissionSearch) => set({ permissionSearch }),

  userSort: { field: "id", order: "asc" },
  toggleUserSort: (field) => set((state) => ({ userSort: toggleSort(state.userSort, field) })),
  roleSort: { field: "id", order: "asc" },
  toggleRoleSort: (field) => set((state) => ({ roleSort: toggleSort(state.roleSort, field) })),
  permissionSort: { field: "id", order: "asc" },
  togglePermissionSort: (field) =>
    set((state) => ({
      permissionSort: toggleSort(state.permissionSort, field),
    })),

  // User dialog state
  createUserOpen: false,
  setCreateUserOpen: (createUserOpen) => set({ createUserOpen }),
  createUserLoading: false,
  setCreateUserLoading: (createUserLoading) => set({ createUserLoading }),
  editUserOpen: false,
  setEditUserOpen: (editUserOpen) => set({ editUserOpen }),
  editUserLoading: false,
  setEditUserLoading: (editUserLoading) => set({ editUserLoading }),
  editUserId: null,
  setEditUserId: (editUserId) => set({ editUserId }),
  editUserUsername: "",
  setEditUserUsername: (editUserUsername) => set({ editUserUsername }),
  editUserEmail: "",
  setEditUserEmail: (editUserEmail) => set({ editUserEmail }),
  editUserYn: 1,
  setEditUserYn: (editUserYn) => set({ editUserYn }),
  editUserRoles: [],
  setEditUserRoles: (editUserRoles) => set({ editUserRoles }),
  originalUserRoles: [],
  setOriginalUserRoles: (originalUserRoles) => set({ originalUserRoles }),
  editUserRelationOpen: false,
  setEditUserRelationOpen: (editUserRelationOpen) => set({ editUserRelationOpen }),

  // Role dialog state
  createRoleOpen: false,
  setCreateRoleOpen: (createRoleOpen) => set({ createRoleOpen }),
  createRoleLoading: false,
  setCreateRoleLoading: (createRoleLoading) => set({ createRoleLoading }),
  editRoleOpen: false,
  setEditRoleOpen: (editRoleOpen) => set({ editRoleOpen }),
  editRoleLoading: false,
  setEditRoleLoading: (editRoleLoading) => set({ editRoleLoading }),
  editRoleId: null,
  setEditRoleId: (editRoleId) => set({ editRoleId }),
  editRoleName: "",
  setEditRoleName: (editRoleName) => set({ editRoleName }),
  editRoleYn: 1,
  setEditRoleYn: (editRoleYn) => set({ editRoleYn }),
  editRoleUsers: [],
  setEditRoleUsers: (editRoleUsers) => set({ editRoleUsers }),
  originalRoleUsers: [],
  setOriginalRoleUsers: (originalRoleUsers) => set({ originalRoleUsers }),
  editRolePermissions: [],
  setEditRolePermissions: (editRolePermissions) => set({ editRolePermissions }),
  originalRolePermissions: [],
  setOriginalRolePermissions: (originalRolePermissions) => set({ originalRolePermissions }),
  editRoleRelationOpen: false,
  setEditRoleRelationOpen: (editRoleRelationOpen) => set({ editRoleRelationOpen }),
  editRoleRelationTab: "users",
  setEditRoleRelationTab: (editRoleRelationTab) => set({ editRoleRelationTab }),

  // Permission dialog state
  createPermissionOpen: false,
  setCreatePermissionOpen: (createPermissionOpen) => set({ createPermissionOpen }),
  createPermissionLoading: false,
  setCreatePermissionLoading: (createPermissionLoading) => set({ createPermissionLoading }),
  editPermissionOpen: false,
  setEditPermissionOpen: (editPermissionOpen) => set({ editPermissionOpen }),
  editPermissionLoading: false,
  setEditPermissionLoading: (editPermissionLoading) => set({ editPermissionLoading }),
  editPermissionId: null,
  setEditPermissionId: (editPermissionId) => set({ editPermissionId }),
  editPermissionName: "",
  setEditPermissionName: (editPermissionName) => set({ editPermissionName }),
  editPermissionDesc: "",
  setEditPermissionDesc: (editPermissionDesc) => set({ editPermissionDesc }),
  editPermissionYn: 1,
  setEditPermissionYn: (editPermissionYn) => set({ editPermissionYn }),
  editPermissionRoles: [],
  setEditPermissionRoles: (editPermissionRoles) => set({ editPermissionRoles }),
  originalPermissionRoles: [],
  setOriginalPermissionRoles: (originalPermissionRoles) => set({ originalPermissionRoles }),
  editPermissionRelationOpen: false,
  setEditPermissionRelationOpen: (editPermissionRelationOpen) =>
    set({ editPermissionRelationOpen }),
}));
