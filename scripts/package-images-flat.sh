#!/usr/bin/env bash
# 扁平化导出镜像 → 单层 tar.gz（避免 FROM 上一版层叠导致 docker save 越打越大）
# 用法: bash scripts/package-images-flat.sh clpm-backend:full-1049 clpm-frontend:full-1049 [out.tar.gz]
set -euo pipefail
BE_IMG=${1:?backend image}
FE_IMG=${2:?frontend image}
OUT=${3:-clpm-images-flat.tar.gz}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

flat_one() {
  local img=$1 tag=$2
  local cid root
  cid=$(docker create "$img")
  root="$WORK/${tag}.tar.gz"
  echo "export $img ..."
  docker export "$cid" | gzip >"$root"
  docker rm "$cid" >/dev/null
  # 恢复运行元数据后重新打成单层镜像
  case "$tag" in
    backend)
      docker import \
        --change 'USER clpm' \
        --change 'WORKDIR /app' \
        --change 'ENV PATH=/app/.venv/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/sbin:/bin' \
        --change 'ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1' \
        --change 'EXPOSE 7101' \
        --change 'CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","7101","--workers","4"]' \
        --change 'HEALTHCHECK --interval=30s --timeout=10s --start-period=40s CMD curl -fsS http://localhost:7101/health || exit 1' \
        "$root" "${img}-flat"
      ;;
    frontend)
      docker import \
        --change 'ENV TZ=Asia/Shanghai' \
        --change 'EXPOSE 7141' \
        --change 'ENTRYPOINT ["/docker-entrypoint.sh"]' \
        --change 'CMD ["nginx","-g","daemon off;"]' \
        --change 'HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS http://localhost:7141/ >/dev/null 2>&1 || exit 1' \
        "$root" "${img}-flat"
      ;;
  esac
}

flat_one "$BE_IMG" backend
flat_one "$FE_IMG" frontend
# 用扁平镜像名覆盖原 tag，save 仅含单层
docker tag "${BE_IMG}-flat" "$BE_IMG"
docker tag "${FE_IMG}-flat" "$FE_IMG"
docker rmi "${BE_IMG}-flat" "${FE_IMG}-flat" >/dev/null 2>&1 || true
docker save "$BE_IMG" "$FE_IMG" | gzip >"$OUT"
echo "package: $OUT"
shasum -a 256 "$OUT"
ls -lh "$OUT"
