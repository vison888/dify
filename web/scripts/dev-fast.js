#!/usr/bin/env node
/**
 * 快速开发脚本 - 避免按需编译
 * 适用于Windows系统
 */

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

console.log('🚀 启动快速开发模式...\n');

// 检查是否存在有效的构建文件
const buildPath = path.join(process.cwd(), '.next');
const buildIdPath = path.join(buildPath, 'BUILD_ID');
const hasValidBuild = fs.existsSync(buildPath) && fs.existsSync(buildIdPath);
const shouldBuild = !hasValidBuild || process.argv.includes('--rebuild');

if (shouldBuild) {
  if (!hasValidBuild) {
    console.log('⚠️  未找到有效的构建文件，开始构建...');
  } else {
    console.log('🔄 强制重新构建...');
  }
  console.log('📦 正在构建项目...');
  const buildProcess = spawn('pnpm', ['build'], {
    stdio: 'inherit',
    shell: true,
    cwd: process.cwd()
  });

  buildProcess.on('close', (code) => {
    if (code === 0) {
      console.log('✅ 构建完成！');
      startServer();
    } else {
      console.error('❌ 构建失败！');
      process.exit(1);
    }
  });
} else {
  console.log('⚡ 使用已存在的构建文件...');
  startServer();
}

function startServer() {
  console.log('🌟 启动开发服务器...\n');
  const serverProcess = spawn('pnpm', ['start:local'], {
    stdio: 'inherit',
    shell: true,
    cwd: process.cwd()
  });

  // 处理进程退出
  process.on('SIGINT', () => {
    console.log('\n👋 正在关闭服务器...');
    serverProcess.kill('SIGINT');
    process.exit(0);
  });

  serverProcess.on('close', (code) => {
    console.log(`服务器已关闭，退出码: ${code}`);
    process.exit(code);
  });
}