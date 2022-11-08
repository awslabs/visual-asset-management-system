import loginBgImageSrc from "../resources/img/login_bg.png";

export function Header() {
  return (
    <>
      <div
        style={{
          position: "fixed",
          backgroundColor: "#16191f",
          top: 0,
          left: 0,
          width: "100vw",
          height: "100vh",
          zIndex: "-200",
        }}
      />
      <img
        src={loginBgImageSrc}
        style={{
          position: "fixed",
          top: 0,
          width: "100vw",
          left: 0,
          zIndex: "-100",
        }}
      />
    </>
  );
}
