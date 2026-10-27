# -*- coding: utf-8 -*-
"""
Pegar imagenes en el chat con Ctrl+V.
===============================================================================

EL PROBLEMA
-----------
Streamlit no sabe nada del portapapeles. `st.file_uploader` obliga a abrir el
explorador de archivos y elegir, que es justo lo que NO se quiere: una captura
de pantalla hay que guardarla antes en disco para poder subirla.

COMO SE RESUELVE
----------------
Un trozo de JavaScript escucha el evento `paste` del navegador. Cuando lo que
se pega es una imagen, la manda a la API del proyecto, que la guarda en un
buzon en memoria. Streamlit recoge de ese buzon en la siguiente pasada y se la
entrega al agente junto con la pregunta.

POR QUE EL JAVASCRIPT ESCUCHA EN LA VENTANA DE ARRIBA
-----------------------------------------------------
`st.components.v1.html` dibuja el codigo dentro de un iframe. Si el oyente se
pusiera en el propio iframe, no se enteraria de nada: cuando alguien pega, el
cursor esta en la caja de escribir, que vive en la pagina de fuera. Por eso el
oyente se engancha a `window.parent.document`.

Eso funciona porque Streamlit crea el iframe con `srcdoc`, de modo que comparte
origen con la pagina principal. Si algun dia dejara de compartirlo, el acceso
fallaria; por eso todo va dentro de un `try` y, si no se puede, se avisa en vez
de romper la pagina.

COMO SE ENTERA STREAMLIT
------------------------
Despues de guardar la imagen, el script busca un boton escondido en la pagina y
lo pulsa. Ese boton no hace nada por si mismo: existe para provocar una pasada
nueva, que es cuando Streamlit mira el buzon.

Si el boton no se encuentra (porque Streamlit cambio su HTML en otra version),
no se pierde nada: la imagen ya esta guardada y se recoge igualmente en la
siguiente pasada, que ocurre en cuanto se envia el mensaje. Se degrada, no se
rompe.

===============================================================================
"""

import json

#: Texto del boton escondido que provoca el redibujado. Tiene que coincidir
#: exactamente con el que crea la interfaz.
ETIQUETA_BOTON = "refrescar-pegado"

#: Alto del componente. Es invisible, pero Streamlit reserva el hueco igual,
#: asi que se deja en lo minimo para que no abra un espacio en blanco.
ALTO = 0


def guion(url_api: str, session_id: str) -> str:
    """
    Arma el HTML con el oyente del portapapeles.

    Args:
        url_api: Direccion de la API tal como la ve el NAVEGADOR. Ojo: no es
            la que usa Streamlit por dentro. Dentro de Docker, Streamlit habla
            con `http://api:8000`, un nombre que solo existe en la red de
            contenedores; el navegador de quien usa la pagina tiene que ir a
            `http://localhost:8000`.
        session_id: Sesion a la que pertenecen las imagenes, para que dos
            pestanas abiertas no se mezclen.

    Returns:
        Codigo HTML listo para `st.components.v1.html`.
    """
    datos = json.dumps({"api": url_api.rstrip("/"), "sesion": session_id})
    return """
<script>
(function () {
  const CFG = __CONFIG__;
  const ETIQUETA = __ETIQUETA__;

  // El iframe comparte origen con la pagina de fuera, asi que se puede
  // escuchar alli. Si no se pudiera, se avisa por consola y se deja estar.
  let doc;
  try {
    doc = window.parent.document;
  } catch (e) {
    console.warn("[pegar] sin acceso a la pagina principal:", e);
    return;
  }

  // Un solo oyente aunque Streamlit vuelva a dibujar el componente.
  if (doc.__pegadoCopiloto) { return; }
  doc.__pegadoCopiloto = true;

  function avisar(texto, color) {
    let caja = doc.getElementById("aviso-pegado-copiloto");
    if (!caja) {
      caja = doc.createElement("div");
      caja.id = "aviso-pegado-copiloto";
      caja.style.cssText =
        "position:fixed;bottom:92px;right:24px;z-index:99999;" +
        "padding:8px 14px;border-radius:8px;font:13px/1.4 sans-serif;" +
        "color:#fff;box-shadow:0 2px 10px rgba(0,0,0,.35);opacity:0;" +
        "transition:opacity .2s";
      doc.body.appendChild(caja);
    }
    caja.style.background = color;
    caja.textContent = texto;
    caja.style.opacity = "1";
    clearTimeout(caja.__t);
    caja.__t = setTimeout(function () { caja.style.opacity = "0"; }, 2600);
  }

  // Pulsa el boton escondido para que Streamlit vuelva a dibujar y mire el
  // buzon. Si no esta, no pasa nada: la imagen ya quedo guardada.
  function redibujar() {
    const botones = doc.querySelectorAll("button");
    for (const b of botones) {
      if ((b.innerText || "").trim() === ETIQUETA) { b.click(); return true; }
    }
    return false;
  }

  // El portapapeles entrega casi siempre un archivo llamado "image.png",
  // asi que pegar tres capturas distintas daba tres nombres identicos. Se le
  // pone la hora para poder distinguirlas de un vistazo.
  function nombrar(archivo) {
    const original = archivo.name || "";
    if (original && original !== "image.png") { return original; }
    const t = new Date();
    const dos = n => String(n).padStart(2, "0");
    const ext = (archivo.type || "image/png").split("/")[1] || "png";
    return "pegada-" + dos(t.getHours()) + dos(t.getMinutes()) +
           dos(t.getSeconds()) + "." + ext;
  }

  function enviar(archivo) {
    const lector = new FileReader();
    lector.onload = function () {
      // El resultado viene como "data:image/png;base64,AAAA..."; la API
      // espera solo la parte de despues de la coma.
      const base64 = String(lector.result).split(",")[1] || "";
      fetch(CFG.api + "/chat/imagen", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: CFG.sesion,
          nombre: nombrar(archivo),
          tipo: archivo.type || "image/png",
          datos: base64
        })
      })
      .then(function (r) {
        if (r.ok) { return r.json(); }
        return r.json().then(function (d) {
          throw new Error(d.detail || ("HTTP " + r.status));
        });
      })
      .then(function (d) {
        avisar("Imagen pegada (" + d.en_espera + " en espera)", "#1f7a3f");
        if (!redibujar()) {
          avisar("Imagen pegada. Escribe y envia.", "#1f7a3f");
        }
      })
      .catch(function (e) {
        avisar("No se pudo pegar: " + e.message, "#9b2226");
        console.error("[pegar]", e);
      });
    };
    lector.onerror = function () {
      avisar("No se pudo leer la imagen del portapapeles", "#9b2226");
    };
    lector.readAsDataURL(archivo);
  }

  doc.addEventListener("paste", function (ev) {
    const porta = ev.clipboardData;
    if (!porta) { return; }

    // Se recorren los elementos del portapapeles buscando imagenes. Al copiar
    // de un editor suelen venir dos cosas a la vez: la imagen y su HTML. Solo
    // interesa la imagen; si no hay ninguna, se deja que el pegado de texto
    // siga su curso normal.
    const imagenes = [];
    for (const elemento of porta.items || []) {
      if (elemento.kind === "file" && (elemento.type || "").startsWith("image/")) {
        const archivo = elemento.getAsFile();
        if (archivo) { imagenes.push(archivo); }
      }
    }
    if (!imagenes.length) { return; }

    ev.preventDefault();
    imagenes.forEach(enviar);
  }, true);

  console.log("[pegar] listo: Ctrl+V pega imagenes en el chat");
})();
</script>
""".replace("__CONFIG__", datos).replace("__ETIQUETA__",
                                         json.dumps(ETIQUETA_BOTON))
