const messages = [
  "Your page is working, and now it has a tiny gecko cheering for it.",
  "The gecko approves this successful test.",
  "Everything is connected. Tiny paws, big victory.",
  "Your setup looks good. The gecko says boop."
];

const bubbleWords = ["boop", "yay", "hi", "wow"];

const statusText = document.querySelector("#status-text");
const message = document.querySelector("#message");
const helloButton = document.querySelector("#hello-button");
const bubble = document.querySelector(".bubble");

let currentMessage = 0;
let currentBubble = 0;

document.addEventListener("DOMContentLoaded", () => {
  if (statusText) {
    statusText.textContent = "all systems cute";
  }
});

if (helloButton && message && bubble) {
  helloButton.addEventListener("click", () => {
    currentMessage = (currentMessage + 1) % messages.length;
    currentBubble = (currentBubble + 1) % bubbleWords.length;

    message.textContent = messages[currentMessage];
    bubble.textContent = bubbleWords[currentBubble];

    document.body.classList.remove("party");
    void document.body.offsetWidth;
    document.body.classList.add("party");
  });
}
