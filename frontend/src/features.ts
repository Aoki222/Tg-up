/**
 * Google Drive 入口开关。
 * false 时设置页不显示该页签，路径目标里也不列出 Drive 文件夹。
 * 这是编译期常量：改完需要重新构建前端，保存设置不会切换它。
 * 已经写在 upload.toml 里的 gdrive 路由仍然有效，只是界面上不再提供入口。
 */
export const ENABLE_GOOGLE_DRIVE = false;
