((document, location, body) => {
  let blocked = null;
  if (document.querySelector("#captchacharacters, form[action*='validateCaptcha']") || /robot check|verify you are human/i.test(body)) blocked = "captcha";
  if (/confirm your age|verify your age|age verification|确认.*年龄/i.test(body)) blocked = "age_verification";
  if (location.pathname.includes("/ap/signin")) blocked = "login_required";
  if (location.pathname.startsWith("/checkpoint/") || document.querySelector('form[action*="/checkpoint/validate"]') || /check the box to continue|\bi am human\b/i.test(body)) blocked = "human_verification";
  if (/sorry.*something went wrong|service unavailable|access denied/i.test(body)) blocked = "access_denied";
  return blocked;
})