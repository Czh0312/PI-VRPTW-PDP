# 清理前快照

before_cleanup.tar.gz 保存清理前的工作区文件，包括未提交源码、新旧结果、文档和已有 archive；不包含 Git 数据库、虚拟环境或缓存。
manifest.json 记录每个文件的 SHA256 和压缩包 SHA256。创建后已逐文件读回校验。
cleanup_actions.json 记录第一轮归档与隔离操作，git_status_before.txt 保留当时 Git 状态。

恢复时先解压到一个新的临时目录，再选择所需文件；不要直接覆盖当前工作区。
例如在项目根执行：

```sh
mkdir /tmp/vrp-before-cleanup
 tar -xzf archive/cleanup_20260929/before_cleanup.tar.gz -C /tmp/vrp-before-cleanup
```

压缩包是本地恢复材料；目录沿用 archive 忽略规则，不会自动进入 Git 提交。
