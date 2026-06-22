const messageBox = document.getElementById("message");
const countdowns = new Map();

function showMessage(message, type = "error") {
  messageBox.textContent = message || "";
  messageBox.dataset.type = type;
}

function setBusy(button, busyText) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = busyText;
  return () => {
    button.disabled = false;
    button.textContent = original;
  };
}

function switchTab(name) {
  showMessage("");
  document.querySelectorAll("[data-auth-tab]").forEach(button => {
    const active = button.dataset.authTab === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("[data-auth-panel]").forEach(panel => {
    panel.hidden = panel.dataset.authPanel !== name;
  });
  document.querySelector(`[data-auth-panel="${name}"] input`)?.focus();
}

function startCountdown(button, seconds) {
  const key = `${button.dataset.purpose}:${button.closest("form").id}`;
  clearInterval(countdowns.get(key));
  let remaining = Number(seconds) || 60;
  button.disabled = true;
  button.textContent = `${remaining}s 后重试`;
  const timer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(timer);
      countdowns.delete(key);
      button.disabled = false;
      button.textContent = "获取验证码";
      return;
    }
    button.textContent = `${remaining}s 后重试`;
  }, 1000);
  countdowns.set(key, timer);
}

async function sendCode(button) {
  const form = button.closest("form");
  const email = new FormData(form).get("email");
  if (!email) {
    showMessage("请先输入邮箱");
    return;
  }
  const restore = setBusy(button, "发送中...");
  try {
    const result = await api("/api/auth/email-code", {
      method: "POST",
      body: {email, purpose: button.dataset.purpose},
    });
    toast("验证码已发送，请查看邮箱");
    showMessage("验证码已发送，请在有效期内完成操作。", "success");
    startCountdown(button, result.cooldown_seconds || 60);
  } catch (error) {
    showMessage(error.message);
    toast(error.message, "error");
  } finally {
    if (!countdowns.has(`${button.dataset.purpose}:${form.id}`)) restore();
  }
}

function saveSessionAndEnter(response) {
  sessionStorage.setItem("csrfToken", response.csrf_token);
  location.href = "/";
}

document.querySelectorAll("[data-auth-tab]").forEach(button => {
  button.addEventListener("click", () => switchTab(button.dataset.authTab));
});

document.querySelectorAll("[data-toggle-password]").forEach(button => {
  button.addEventListener("click", event => {
    const input = event.currentTarget.parentElement.querySelector("input");
    input.type = input.type === "password" ? "text" : "password";
    event.currentTarget.textContent = input.type === "password" ? "显示" : "隐藏";
  });
});

document.querySelectorAll("[data-send-code]").forEach(button => {
  button.addEventListener("click", () => sendCode(button));
});

document.getElementById("passwordLoginForm").addEventListener("submit", async event => {
  event.preventDefault();
  showMessage("");
  const restore = setBusy(event.submitter, "正在登录...");
  try {
    const response = await api("/api/auth/login", {method: "POST", body: Object.fromEntries(new FormData(event.target))});
    saveSessionAndEnter(response);
  } catch (error) {
    showMessage(error.message);
  } finally {
    restore();
  }
});

document.getElementById("emailLoginForm").addEventListener("submit", async event => {
  event.preventDefault();
  showMessage("");
  const restore = setBusy(event.submitter, "正在登录...");
  try {
    const response = await api("/api/auth/email-login", {method: "POST", body: Object.fromEntries(new FormData(event.target))});
    saveSessionAndEnter(response);
  } catch (error) {
    showMessage(error.message);
  } finally {
    restore();
  }
});

document.getElementById("resetPasswordForm").addEventListener("submit", async event => {
  event.preventDefault();
  showMessage("");
  const restore = setBusy(event.submitter, "正在重置...");
  try {
    await api("/api/auth/password-reset", {method: "POST", body: Object.fromEntries(new FormData(event.target))});
    event.target.reset();
    toast("密码已重置，请使用新密码登录");
    switchTab("password");
  } catch (error) {
    showMessage(error.message);
  } finally {
    restore();
  }
});
