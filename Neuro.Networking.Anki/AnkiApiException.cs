using System;
using System.Net;

namespace Neuro.Networking.Anki
{
    public sealed class AnkiApiException : Exception
    {
        public AnkiApiException(HttpStatusCode StatusCode, string ResponseBody)
            : base($"Anki bridge returned HTTP {(int)StatusCode} ({StatusCode}).")
        {
            this.StatusCode = StatusCode;
            this.ResponseBody = ResponseBody;
        }
        public HttpStatusCode StatusCode { get; }
        /// <summary>Structured error JSON. Not included in the exception message or sniffer output.</summary>
        public string ResponseBody { get; }
    }
}
