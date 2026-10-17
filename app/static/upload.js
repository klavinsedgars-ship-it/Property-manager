// Keep uploads under the server limit (Vercel: 4.5 MB per request).
// Photos are downscaled in the browser; other oversized files are rejected with a message.
document.addEventListener("change", async (e) => {
  const input = e.target;
  if (!(input instanceof HTMLInputElement) || input.type !== "file" || !input.dataset.maxMb) return;
  const limit = parseFloat(input.dataset.maxMb) * 1024 * 1024;
  const file = input.files && input.files[0];
  if (!file || file.size <= limit) return;
  if (!file.type.startsWith("image/") || file.type === "image/gif") {
    alert(`"${file.name}" is ${(file.size / 1048576).toFixed(1)} MB — the limit is ${input.dataset.maxMb} MB. Please compress it first.`);
    input.value = "";
    return;
  }
  try {
    const img = await createImageBitmap(file);
    let scale = Math.min(1, 2400 / Math.max(img.width, img.height));
    let blob;
    for (let q = 0.85; q >= 0.4; q -= 0.15) {
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      blob = await new Promise((r) => c.toBlob(r, "image/jpeg", q));
      if (blob.size <= limit) break;
      scale *= 0.8;
    }
    if (!blob || blob.size > limit) throw new Error("still too large");
    const dt = new DataTransfer();
    dt.items.add(new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }));
    input.files = dt.files;
  } catch (err) {
    alert(`Could not shrink "${file.name}" below ${input.dataset.maxMb} MB.`);
    input.value = "";
  }
});
