/*
 * hdemg_frame.h  -  Shared wire format for the RHD2164 -> STM32H745 -> ESP32-S3 -> PC link.
 *
 * One header, three consumers:
 *   - STM32H745 firmware  (packs frames, sends over SPI4 to the ESP)
 *   - ESP32-S3 firmware   (forwards frames verbatim over TCP/UDP; may set t_esp)
 *   - PC host tools        (emu_verify.py / latency_profiler.py parse this layout)
 *
 * The pipeline supports BOTH raw streaming and the RMS envelope. The `type`
 * field tells the receiver which it is, so you can switch at runtime (or build
 * with HDEMG_MODE_*) without the PC guessing. RMS was the original test mode;
 * RAW is what makes sample-by-sample emulator verification possible.
 *
 * All multi-byte fields little-endian (STM32 + ESP32 + x86 are all LE -> no swaps).
 */
#ifndef HDEMG_FRAME_H
#define HDEMG_FRAME_H

#include <stdint.h>

#define HDEMG_MAGIC        0xA55Au

/* payload type */
#define HDEMG_TYPE_RAW16   0u   /* int16 per channel, one sample period           */
#define HDEMG_TYPE_RMS16   1u   /* int16 RMS per channel, one decimation window    */

/* chip_id */
#define HDEMG_CHIP_0       0u
#define HDEMG_CHIP_1       1u
#define HDEMG_CHIP_BOTH    0xFFu /* combined: chip0 A,chip0 B,chip1 A,chip1 B      */

/* 14-byte header, then n_ch * int16 payload. __packed so SPI/TCP see it raw. */
typedef struct __attribute__((packed)) {
    uint16_t magic;     /* = HDEMG_MAGIC (0xA55A)                                  */
    uint8_t  type;      /* HDEMG_TYPE_*                                            */
    uint8_t  chip_id;   /* HDEMG_CHIP_*                                            */
    uint32_t seq;       /* monotonic frame counter (loss detection)               */
    uint32_t t_stm;     /* DWT->CYCCNT latched at sample period (latency anchor)   */
    uint16_t n_ch;      /* channels in payload                                     */
    /* int16_t payload[n_ch] follows immediately */
} hdemg_hdr_t;

#define HDEMG_HDR_BYTES        ((uint32_t)sizeof(hdemg_hdr_t))           /* 14 */
#define HDEMG_FRAME_BYTES(nch) (HDEMG_HDR_BYTES + (uint32_t)(nch) * 2u)

/*
 * Channel order for combined (HDEMG_CHIP_BOTH) frames -- must match
 * emu_verify.py build_expected():
 *     [0..31]   chip0 module A (amp ch 0..31)
 *     [32..63]  chip0 module B (amp ch 32..63)
 *     [64..95]  chip1 module A
 *     [96..127] chip1 module B
 * For a single chip: A(0..31) then B(0..31).
 */

#ifdef __cplusplus
extern "C" {
#endif

/* Fill a header in-place. Returns total frame size in bytes. */
static inline uint32_t hdemg_write_hdr(void *dst, uint8_t type, uint8_t chip_id,
                                       uint32_t seq, uint32_t t_stm, uint16_t n_ch)
{
    hdemg_hdr_t *h = (hdemg_hdr_t *)dst;
    h->magic   = HDEMG_MAGIC;
    h->type    = type;
    h->chip_id = chip_id;
    h->seq     = seq;
    h->t_stm   = t_stm;
    h->n_ch    = n_ch;
    return HDEMG_FRAME_BYTES(n_ch);
}

#ifdef __cplusplus
}
#endif
#endif /* HDEMG_FRAME_H */
